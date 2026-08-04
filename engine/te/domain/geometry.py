"""`ExitGeometry` — ONE representation of where a position's exits sit.

Exit levels were expressed two ways at once: absolute index-point distances
(`stop_distance`/`target_distance`/`trailing_distance`) and percentages of the
option premium (`stop_pct`/`target_pct`/`trailing_pct`), resolved by an
`if ... is not None` at each use site. Nothing forced the two to be
consistent or complete, so `stop_pct` set with `target_pct` unset produced a
stop in premium-% and a target in index points on the same position, and no
constructor objected.

That ambiguity had teeth. Setting `paper_cycle_trailing_pct = None` to
DISABLE the trail — so live behaviour would match a barrier sweep that
modelled only stop/target/time — instead fell through to the leftover
`trailing_distance = 300`, restoring a Rs 3 absolute trail. On that day's Rs
81.50 NIFTY premium that is 3.68%, and it is the same Rs 3 trail that had
closed 14 of 14 trades on `trailing_stop` at a 3.1-minute average hold.
"Disabled" re-enabled the original bug, and only a review caught it.

So the two forms become two VARIANTS of one type, and mixing them is
unrepresentable rather than merely discouraged:

* `PremiumPercentGeometry` — what live trading uses. Percentages of the
  option premium, because a fixed Rs 15 target is 50% of a Rs 30 premium and
  5% of a Rs 300 one, so an absolute distance silently changes the strategy
  as premiums move. `trailing_pct=None` here means the trail is OFF, with
  nothing to fall back to.
* `AbsolutePointGeometry` — index-point distances, for backtests replayed
  from `option_bhav` and for every test written before percentages existed.

Both answer one question, `levels(entry_premium)`, so callers never branch on
which form is in use.

Lives in `te.domain` (pure, no I/O) because both `te.engine.cycle` and
`te.backtest.engine` must hold the SAME object — the backtest is meant to
reproduce what the live cycle would have decided, and it previously drifted
by simply not having the newer fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from te.domain.money import Paise


def pct_of(premium: Paise, pct: Decimal) -> Paise:
    """`pct` percent of `premium`, TRUNCATED toward zero.

    One definition rather than one per call site: the rounding is the
    load-bearing part, and restating it is how a stop and a target quietly
    stop agreeing about the same premium."""
    return Paise(int(Decimal(int(premium)) * pct / Decimal(100)))


@dataclass(frozen=True)
class ExitLevels:
    """Resolved absolute levels for one entry."""

    stop: Paise
    target: Paise
    trailing_distance: Paise | None
    #: Premium at which the ONE-TIME profit lock engages — see
    #: `te.engine.exits.evaluate_position`'s profit-lock check. `None` means
    #: the rule is off. Distinct from `trailing_distance`, which ratchets
    #: forever once activated; this fires exactly once and then freezes,
    #: per the 2026-08-04 backtest that validated this specific shape
    #: (activation=15%, buffer=5% of NIFTY option premium): it does not
    #: improve mean R (roughly flat, -0.104R vs -0.102R baseline over 1,305
    #: real trades) but trades some large winners for a materially higher
    #: win rate (50.7% vs 46.2%) and removes the tail risk of a big winner
    #: round-tripping all the way back to the original stop. A deliberate
    #: risk-shaping choice, not a claimed profitability improvement.
    profit_lock_activation: Paise | None = None
    #: How far below the price AT ACTIVATION the locked stop sits, as a
    #: percent of THAT price (not the entry premium) — kept as a raw
    #: percentage because the actual lock level cannot be known at entry
    #: time, only once the activation price is actually observed live.
    profit_lock_buffer_pct: Decimal | None = None


@dataclass(frozen=True)
class PremiumPercentGeometry:
    """Stop/target/trail as percentages of the ENTRY PREMIUM.

    `trailing_pct=None` means the trailing stop is disabled — there is no
    other source for it to fall back to, which is the entire point of this
    type existing.
    """

    stop_pct: Decimal
    target_pct: Decimal
    trailing_pct: Decimal | None = None
    #: See `ExitLevels.profit_lock_activation`. Both must be set together
    #: (or both left `None`) — a buffer with no activation, or vice versa,
    #: is a half-specified rule with no sane default for the other half.
    profit_lock_activation_pct: Decimal | None = None
    profit_lock_buffer_pct: Decimal | None = None

    def __post_init__(self) -> None:
        if self.stop_pct <= 0 or self.stop_pct >= 100:
            raise ValueError(f"stop_pct must be in (0, 100), got {self.stop_pct}")
        if self.target_pct <= 0:
            raise ValueError(f"target_pct must be positive, got {self.target_pct}")
        if self.trailing_pct is not None and self.trailing_pct <= 0:
            raise ValueError(f"trailing_pct must be positive when set, got {self.trailing_pct}")
        if (self.profit_lock_activation_pct is None) != (self.profit_lock_buffer_pct is None):
            raise ValueError("profit_lock_activation_pct and profit_lock_buffer_pct must be set together")
        if self.profit_lock_activation_pct is not None and self.profit_lock_activation_pct <= 0:
            raise ValueError(f"profit_lock_activation_pct must be positive, got {self.profit_lock_activation_pct}")
        if self.profit_lock_buffer_pct is not None and not (0 < self.profit_lock_buffer_pct < 100):
            raise ValueError(f"profit_lock_buffer_pct must be in (0, 100), got {self.profit_lock_buffer_pct}")

    def levels(self, entry_premium: Paise) -> ExitLevels:
        return ExitLevels(
            stop=Paise(entry_premium - pct_of(entry_premium, self.stop_pct)),
            target=Paise(entry_premium + pct_of(entry_premium, self.target_pct)),
            trailing_distance=None if self.trailing_pct is None else pct_of(entry_premium, self.trailing_pct),
            profit_lock_activation=(
                None
                if self.profit_lock_activation_pct is None
                else Paise(entry_premium + pct_of(entry_premium, self.profit_lock_activation_pct))
            ),
            profit_lock_buffer_pct=self.profit_lock_buffer_pct,
        )


@dataclass(frozen=True)
class AbsolutePointGeometry:
    """Stop/target/trail as absolute distances in the traded price's own
    units. Correct when the instrument's price scale is fixed and known —
    backtests replayed from recorded option bars, and the tests that predate
    percentage exits."""

    stop_distance: Paise
    target_distance: Paise
    trailing_distance: Paise | None = None

    def __post_init__(self) -> None:
        if self.stop_distance <= 0:
            raise ValueError(f"stop_distance must be positive, got {self.stop_distance}")
        if self.target_distance <= 0:
            raise ValueError(f"target_distance must be positive, got {self.target_distance}")
        if self.trailing_distance is not None and self.trailing_distance <= 0:
            raise ValueError(f"trailing_distance must be positive when set, got {self.trailing_distance}")

    def levels(self, entry_premium: Paise) -> ExitLevels:
        # The one-time profit lock (see `ExitLevels.profit_lock_activation`)
        # is a `PremiumPercentGeometry`-only feature — this type is used for
        # backtests replayed from `option_bhav` and pre-percentage tests,
        # never live, so there has been no need for it here. `ExitLevels`
        # stays one shape either way; this variant just always leaves those
        # two fields `None`.
        return ExitLevels(
            stop=Paise(entry_premium - self.stop_distance),
            target=Paise(entry_premium + self.target_distance),
            trailing_distance=self.trailing_distance,
        )


#: Either variant. A union rather than a base class: there are exactly two
#: ways to express this and both are closed, so a `Protocol` would only add
#: the ability to define a third somewhere unreviewed.
type ExitGeometry = PremiumPercentGeometry | AbsolutePointGeometry
