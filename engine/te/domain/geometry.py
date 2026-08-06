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
* `RupeeRiskGeometry` — the RUPEE loss the owner is prepared to take on one
  trade, converted into a premium distance using the position's own
  quantity. Added 2026-08-06 because neither variant above could express
  "cap my loss at Rs 700": a percentage of premium is a different rupee
  amount on every contract (20% of NIFTY's Rs 166.80 premium is Rs 2,169 at
  65 units; 20% of SENSEX's Rs 283.80 is Rs 1,135 at 20), and an absolute
  premium distance is a different rupee amount for every lot size.

All three answer one question, `levels(entry_premium, quantity=...)`, so
callers never branch on which form is in use. `quantity` is ignored by the
two percentage/point variants and required by `RupeeRiskGeometry` — the one
form whose answer genuinely depends on how many units are being bought.

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

    def levels(self, entry_premium: Paise, *, quantity: int | None = None) -> ExitLevels:
        del quantity  # percentages of premium do not depend on how many units are bought
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

    def levels(self, entry_premium: Paise, *, quantity: int | None = None) -> ExitLevels:
        del quantity  # an absolute premium distance does not depend on quantity either
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


@dataclass(frozen=True)
class RupeeRiskGeometry:
    """The stop is a RUPEE amount for the whole position; everything else is
    derived from it.

    Asked for directly by the owner on 2026-08-06, after a NIFTY trade lost
    Rs 2,272 on a Rs 50,000 account (4.5% in one trade) under a 20% premium
    stop. The request was "buy what the signal picks, but cap my loss at
    Rs 600-700", and neither existing variant can express it:

    * a percentage of premium is a different rupee loss on every contract
    * an absolute premium distance is a different rupee loss on every lot
      size (Rs 10.77 is Rs 700 at NIFTY's 65, Rs 215 at SENSEX's 20)

    So the rupee figure is the INPUT and the premium distance is computed
    from it:

        stop_distance = max_loss_paise // quantity

    `quantity` is `lots x lot_size`, which makes the guarantee exact only if
    the lot count cannot grow — see `te.risk.sizing.size_position`'s
    `max_lots`, which is what pins it. Without that cap the engine divides
    the risk budget by this (now much smaller) per-lot risk and buys MORE
    lots, restoring the original rupee loss under a different name. The two
    changes are one change; neither is correct alone.

    ### The target is a MULTIPLE of the risk, not a percentage of premium

    A 20% stop against a 20% target is 1:1 — every winner is cut at exactly
    the size of every loser, which caps profit by construction. Here the
    target is `target_multiple` x the rupee risk, so raising it is how the
    profit ceiling is lifted without the number ever becoming a fiction: at
    `target_multiple=10` on a Rs 166.80 premium the target is +65% of
    premium, which real intraday options do reach.

    NOT expressed as "no target at all". `ExitLevels.target` is non-optional
    and persisted `NOT NULL`, and a truly uncapped position needs a TRAILING
    stop to protect it — the one exit rule this project has never backtested
    (the last live trail closed 14 of 14 trades at a 3.1-minute average
    hold). A distant real target is the honest interim: it does not claim a
    measurement nobody has made.
    """

    #: What one trade is allowed to lose, in paise, across the whole
    #: position. This is the number the owner set; everything else bends to
    #: keep it true.
    max_loss_paise: int
    #: Target distance as a multiple of the rupee risk. `10` means "risk
    #: Rs 700 to make Rs 7,000".
    target_multiple: Decimal
    trailing_pct: Decimal | None = None
    #: See `ExitLevels.profit_lock_activation`. Both together or neither.
    profit_lock_activation_pct: Decimal | None = None
    profit_lock_buffer_pct: Decimal | None = None

    def __post_init__(self) -> None:
        if self.max_loss_paise <= 0:
            raise ValueError(f"max_loss_paise must be positive, got {self.max_loss_paise}")
        if self.target_multiple <= 0:
            raise ValueError(f"target_multiple must be positive, got {self.target_multiple}")
        if self.trailing_pct is not None and self.trailing_pct <= 0:
            raise ValueError(f"trailing_pct must be positive when set, got {self.trailing_pct}")
        if (self.profit_lock_activation_pct is None) != (self.profit_lock_buffer_pct is None):
            raise ValueError("profit_lock_activation_pct and profit_lock_buffer_pct must be set together")
        if self.profit_lock_activation_pct is not None and self.profit_lock_activation_pct <= 0:
            raise ValueError(f"profit_lock_activation_pct must be positive, got {self.profit_lock_activation_pct}")
        if self.profit_lock_buffer_pct is not None and not (0 < self.profit_lock_buffer_pct < 100):
            raise ValueError(f"profit_lock_buffer_pct must be in (0, 100), got {self.profit_lock_buffer_pct}")

    def levels(self, entry_premium: Paise, *, quantity: int | None = None) -> ExitLevels:
        if quantity is None or quantity <= 0:
            raise ValueError(
                f"RupeeRiskGeometry needs a positive quantity to convert Rs {self.max_loss_paise / 100:.2f} "
                f"of risk into a premium distance, got {quantity!r}"
            )
        # Truncating DOWN is deliberate and is the safe direction: a smaller
        # premium distance means the stop sits closer to entry, so the
        # realised loss lands at or below `max_loss_paise`, never above it.
        stop_distance = self.max_loss_paise // quantity
        if stop_distance <= 0:
            raise ValueError(
                f"Rs {self.max_loss_paise / 100:.2f} spread over {quantity} units is less than one paise "
                f"per unit — no stop can express this risk at this position size"
            )
        if stop_distance >= int(entry_premium):
            # The premium is worth less than the loss being budgeted for, so
            # the whole position is already inside the risk cap: the option
            # cannot fall below zero. Stop at zero rather than at a negative
            # premium, which is not a price.
            stop_distance = int(entry_premium)
        target_distance = int(Decimal(stop_distance) * self.target_multiple)
        return ExitLevels(
            stop=Paise(int(entry_premium) - stop_distance),
            target=Paise(int(entry_premium) + target_distance),
            trailing_distance=None if self.trailing_pct is None else pct_of(entry_premium, self.trailing_pct),
            profit_lock_activation=(
                None
                if self.profit_lock_activation_pct is None
                else Paise(entry_premium + pct_of(entry_premium, self.profit_lock_activation_pct))
            ),
            profit_lock_buffer_pct=self.profit_lock_buffer_pct,
        )


#: Any variant. A union rather than a base class: there are exactly three
#: ways to express this and all are closed, so a `Protocol` would only add
#: the ability to define a fourth somewhere unreviewed.
type ExitGeometry = PremiumPercentGeometry | AbsolutePointGeometry | RupeeRiskGeometry
