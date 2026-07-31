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

    def __post_init__(self) -> None:
        if self.stop_pct <= 0 or self.stop_pct >= 100:
            raise ValueError(f"stop_pct must be in (0, 100), got {self.stop_pct}")
        if self.target_pct <= 0:
            raise ValueError(f"target_pct must be positive, got {self.target_pct}")
        if self.trailing_pct is not None and self.trailing_pct <= 0:
            raise ValueError(f"trailing_pct must be positive when set, got {self.trailing_pct}")

    def levels(self, entry_premium: Paise) -> ExitLevels:
        return ExitLevels(
            stop=Paise(entry_premium - pct_of(entry_premium, self.stop_pct)),
            target=Paise(entry_premium + pct_of(entry_premium, self.target_pct)),
            trailing_distance=None if self.trailing_pct is None else pct_of(entry_premium, self.trailing_pct),
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
        return ExitLevels(
            stop=Paise(entry_premium - self.stop_distance),
            target=Paise(entry_premium + self.target_distance),
            trailing_distance=self.trailing_distance,
        )


#: Either variant. A union rather than a base class: there are exactly two
#: ways to express this and both are closed, so a `Protocol` would only add
#: the ability to define a third somewhere unreviewed.
type ExitGeometry = PremiumPercentGeometry | AbsolutePointGeometry
