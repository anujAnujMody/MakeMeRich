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
from typing import Callable

from te.domain.money import Paise, rupees


class DegenerateGeometry(ValueError):
    """The requested risk cannot be expressed as a stop at this premium and
    position size — the whole premium sits inside the rupee cap (or inside
    an absolute stop distance), so there is no reachable stop and no honest
    target. Subclasses `ValueError`, so any existing caller that catches
    `ValueError` keeps working."""


def pct_of(premium: Paise, pct: Decimal) -> Paise:
    """`pct` percent of `premium`, TRUNCATED toward zero.

    One definition rather than one per call site: the rounding is the
    load-bearing part, and restating it is how a stop and a target quietly
    stop agreeing about the same premium."""
    return Paise(int(Decimal(int(premium)) * pct / Decimal(100)))


def _rupees_str(amount_paise: int) -> str:
    """Integer-paise display formatting for error messages — no float
    division on money, per the project's money rule. Reuses
    `te.domain.money.rupees`, the one existing rupee formatter, rather than
    restating the rounding."""
    return f"Rs {rupees(Paise(amount_paise))}"


@dataclass(frozen=True)
class ExitLevels:
    """Resolved absolute levels for one entry."""

    stop: Paise
    target: Paise
    trailing_distance: Paise | None
    #: Premium at which the profit lock FIRST engages — see
    #: `te.engine.exits.evaluate_position`'s profit-lock check and
    #: `te.engine.exits._next_profit_lock_rung`. `None` means the rule is
    #: off. Distinct from `trailing_distance`, which ratchets continuously
    #: (on every tick); this ratchets in discrete STEPS instead — each
    #: further `profit_lock_activation_pct` gain locks the stop
    #: `profit_lock_buffer_pct` below the price at that moment, then holds
    #: until the next step. It fired exactly once and then froze until
    #: 2026-08-06, when it was changed to a multi-rung ratchet at the
    #: owner's request ("move SL after every +15%") — this field now names
    #: only the FIRST rung, not the whole rule's ceiling. Shape backtested
    #: 2026-08-04 at activation=15%, buffer=5% of NIFTY option premium: it
    #: does not improve mean R (roughly flat, -0.104R vs -0.102R baseline
    #: over 1,305 real trades) but trades some large winners for a
    #: materially higher win rate (50.7% vs 46.2%) and removes the tail risk
    #: of a big winner round-tripping all the way back to the original stop.
    #: A deliberate risk-shaping choice, not a claimed profitability
    #: improvement.
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

    def levels(
        self,
        entry_premium: Paise,
        *,
        quantity: int | None = None,
        cost_estimator: Callable[[Paise], Paise] | None = None,
    ) -> ExitLevels:
        del quantity  # percentages of premium do not depend on how many units are bought
        del cost_estimator  # a percentage of premium does not need a cost-aware fixed point either
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

    def levels(
        self,
        entry_premium: Paise,
        *,
        quantity: int | None = None,
        cost_estimator: Callable[[Paise], Paise] | None = None,
    ) -> ExitLevels:
        del quantity  # an absolute premium distance does not depend on quantity either
        del cost_estimator  # an absolute premium distance is fixed regardless of round-trip cost
        if self.stop_distance >= int(entry_premium):
            raise DegenerateGeometry(
                f"a stop distance of {int(self.stop_distance)}p is at or beyond the entry premium of "
                f"{int(entry_premium)}p — the stop would sit at or below zero, which is not a price"
            )
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

    ### The cap is GROSS unless a `cost_estimator` is supplied

    `stop_distance = max_loss_paise // quantity` alone caps only the PRICE
    move, not the realised loss — round-trip costs (brokerage, STT,
    exchange/SEBI charges, GST, stamp duty) sit on top of it and are not
    free. At a Rs 700 cap, NIFTY's real premium and quantity, and today's
    charges, the gross loss at stop is ~Rs 699 but the real net loss with
    costs is ~Rs 771 — a breach of the cap this class exists to enforce.

    `levels()` therefore accepts an optional `cost_estimator`: given a
    candidate EXIT premium, it returns the round-trip cost in paise for the
    whole position at `quantity` units. When supplied, the stop distance is
    solved by a short fixed-point iteration (cost depends on the exit price,
    which depends on the stop distance) so that gross loss + costs lands at
    or under `max_loss_paise`. When `cost_estimator` is `None` (the
    default), behaviour is exactly the old gross-only one — callers that
    need the true net cap MUST pass an estimator.
    """

    #: What one trade is allowed to lose, in paise, across the whole
    #: position. This is the number the owner set; everything else bends to
    #: keep it true.
    max_loss_paise: Paise
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

    def levels(
        self,
        entry_premium: Paise,
        *,
        quantity: int | None = None,
        cost_estimator: Callable[[Paise], Paise] | None = None,
    ) -> ExitLevels:
        if quantity is None or quantity <= 0:
            raise ValueError(
                f"RupeeRiskGeometry needs a positive quantity to convert {_rupees_str(self.max_loss_paise)} "
                f"of risk into a premium distance, got {quantity!r}"
            )
        # Truncating DOWN is deliberate and is the safe direction: a smaller
        # premium distance means the stop sits closer to entry, so the
        # realised loss lands at or below `max_loss_paise`, never above it.
        stop_distance = self.max_loss_paise // quantity
        if cost_estimator is not None:
            # Costs depend on the exit price, which depends on the stop
            # distance being solved for — so this is a fixed point, not a
            # closed form. Three iterations converge in practice: the cost
            # curve is nearly flat over the small range a stop can move
            # within one position, so each pass changes `d` by less than the
            # last. See the class docstring's "GROSS unless a cost_estimator
            # is supplied" section.
            d = stop_distance
            for _ in range(3):
                candidate_exit = Paise(int(entry_premium) - d)
                c = int(cost_estimator(candidate_exit))
                d = (self.max_loss_paise - c) // quantity
            stop_distance = d
        if stop_distance <= 0:
            if cost_estimator is not None:
                raise ValueError(
                    f"{_rupees_str(self.max_loss_paise)} spread over {quantity} units is smaller than the "
                    "round-trip cost of a trade at this position size — no stop can express this rupee cap "
                    "once real costs are included"
                )
            raise ValueError(
                f"{_rupees_str(self.max_loss_paise)} spread over {quantity} units is less than one paise "
                f"per unit — no stop can express this risk at this position size"
            )
        if stop_distance >= int(entry_premium):
            # The whole premium sits inside the rupee cap: there is no
            # reachable stop (the option cannot print below zero) and,
            # symmetrically, no honest target either. Refusing rather than
            # clamping to a stop at premium zero, which is not a price the
            # option can ever reach.
            raise DegenerateGeometry(
                f"entry premium {int(entry_premium)}p at quantity {quantity} cannot express a "
                f"{_rupees_str(self.max_loss_paise)} rupee cap as a stop — the resolved stop distance "
                f"({stop_distance}p) is at or beyond the entry premium itself"
            )
        target_distance = int(Decimal(stop_distance) * self.target_multiple)
        target = Paise(int(entry_premium) + target_distance)

        # The profit lock is a PERCENTAGE of premium while the target is a
        # multiple of a RUPEE risk, so the two scale differently and the lock
        # can land beyond the target — at which point the target closes the
        # trade first and the lock could never fire once.
        #
        #     unreachable when  premium x lot_size > (multiple / activation%) x max_loss
        #
        # At the 2026-08-06 live values (Rs 700, 10x, +15%) that is a
        # position notional above Rs 46,667. Today the 50%-of-capital cap
        # holds notional under Rs 25,000, so it cannot happen — but that is
        # the position cap protecting a different rule by coincidence, and
        # raising capital to Rs 1,00,000 moves the cap to Rs 50,000 and puts
        # a real signal over the line.
        #
        # Found 2026-08-06 by `scripts/stop_level_report.py`, which crashed
        # on `ExitPlan.__post_init__` at a Rs 300 stop. That validator was
        # right to refuse the plan; the bug was constructing one. Reporting
        # the rule as OFF is the honest answer per `honest-metrics` — a lock
        # that cannot fire must never read as enabled — and it is also the
        # safe one, since the alternative is an exception inside the entry
        # cycle at the moment a position was meant to open.
        activation: Paise | None = None
        buffer_pct = self.profit_lock_buffer_pct
        if self.profit_lock_activation_pct is not None:
            candidate = Paise(entry_premium + pct_of(entry_premium, self.profit_lock_activation_pct))
            if int(candidate) < int(target):
                activation = candidate
            else:
                buffer_pct = None

        return ExitLevels(
            stop=Paise(int(entry_premium) - stop_distance),
            target=target,
            trailing_distance=None if self.trailing_pct is None else pct_of(entry_premium, self.trailing_pct),
            profit_lock_activation=activation,
            profit_lock_buffer_pct=buffer_pct,
        )


#: Any variant. A union rather than a base class: there are exactly three
#: ways to express this and all are closed, so a `Protocol` would only add
#: the ability to define a fourth somewhere unreviewed.
type ExitGeometry = PremiumPercentGeometry | AbsolutePointGeometry | RupeeRiskGeometry
