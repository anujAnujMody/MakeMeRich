"""`size_position()` — turns a signal's premium/stop/target into a lot count,
or a real, always-present reason why it can't be sized. This is the exact
bug class the plan calls out from the old engine: a sizing bug meant it had
never placed a single trade, silently. `lots=0` without a `rejected_reason`
is a bug by definition here — every return path that yields `lots=0` sets
`rejected_reason`, and `lots > 0` always leaves it `None`.

Two independent gates, both real:
1. **Cost-vs-edge floor** — the gross edge per lot ((target - premium) x
   lot_size) must be at least `min_edge_multiple` times the real round-trip
   cost (computed via the actual `CostModel`, never estimated) or the trade
   is rejected outright, regardless of capital/risk budget. This is the
   plan's "₹20-premium trap" gate — fixed ₹20/order brokerage makes cheap
   premiums structurally unprofitable no matter how many lots you buy.
2. **Risk-budget sizing** — `lots` is however many lots fit inside
   `capital x risk_budget_pct` given the real per-lot loss-at-stop
   ((premium - stop) x lot_size), further capped by how many lots the
   capital can actually afford to buy outright (no leverage assumed —
   directional option BUYING only, per the plan).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from te.domain.costs import CostModel
from te.domain.money import Paise


@dataclass(frozen=True)
class SizingResult:
    lots: int
    risk_paise: Paise
    round_trip_cost_paise: Paise
    cost_as_pct_of_risk: Decimal
    rejected_reason: str | None

    def __post_init__(self) -> None:
        if self.lots == 0 and self.rejected_reason is None:
            raise ValueError(
                "SizingResult(lots=0) must always carry a rejected_reason — a silent zero-lot "
                "result is the exact bug class that meant the old engine never placed a trade"
            )
        if self.lots > 0 and self.rejected_reason is not None:
            raise ValueError(
                f"SizingResult(lots={self.lots}) must not carry a rejected_reason, got {self.rejected_reason!r}"
            )


def _rejected(*, round_trip_cost_paise: Paise, reason: str) -> SizingResult:
    return SizingResult(
        lots=0,
        risk_paise=Paise(0),
        round_trip_cost_paise=round_trip_cost_paise,
        cost_as_pct_of_risk=Decimal(0),
        rejected_reason=reason,
    )


def size_position(
    *,
    capital: Paise,
    risk_budget_pct: Decimal,
    premium: Paise,
    stop_premium: Paise,
    target_premium: Paise,
    lot_size: int,
    costs: CostModel,
    exchange: str,
    on: date,
    min_edge_multiple: Decimal,
    max_position_size_pct: Decimal = Decimal(100),
    max_lots: int | None = None,
) -> SizingResult:
    """Rejects (lots=0, rejected_reason set) when the gross edge per lot is
    below `round_trip_cost x min_edge_multiple`, when the risk budget can't
    afford even one lot, or when capital can't afford even one lot outright.
    Never returns `lots=0` without a `rejected_reason` — see
    `SizingResult.__post_init__`.

    `max_position_size_pct` caps a SINGLE position's notional
    (premium x lot_size x lots) at that percentage of `capital` — a
    separate cap from `risk_budget_pct` (which bounds LOSS-AT-STOP, not
    notional exposure): a position can be sized well within its risk
    budget yet still tie up an outsized share of capital in one bet if the
    stop is close to premium. Defaults to `100` (capital-affordability is
    already the effective ceiling, unchanged from before this parameter
    existed) so every pre-existing call site behaves identically.

    `max_lots` is a HARD ceiling applied after the three computed caps, and
    it exists because the others are all divisions by risk-per-lot. Tighten
    the stop and `lots_by_risk = risk_budget // risk_per_lot` grows: a
    Rs 700 per-lot stop against a Rs 2,500 budget buys three lots and loses
    Rs 2,100, so a "Rs 700 stop-loss" silently means "Rs 700 per lot".

    That is not a hypothetical — it is the exact interaction that makes
    `te.domain.geometry.RupeeRiskGeometry` meaningless without this. A rupee
    risk cap and a lot cap are one feature in two files; shipping the first
    alone would produce a setting that reads as a Rs 700 limit and delivers
    a multiple of it.

    `None` (the default) means no ceiling, so every pre-existing call site is
    unchanged. Never rejects on its own: a cap can only reduce a size that
    was already >= 1, so it cannot turn a tradeable signal into a skip."""
    per_lot_round_trip = costs.round_trip(
        entry_premium=premium, exit_premium=target_premium, qty=lot_size, exchange=exchange, on=on
    ).total
    gross_edge_per_lot = (target_premium - premium) * lot_size

    if gross_edge_per_lot <= 0:
        return _rejected(
            round_trip_cost_paise=per_lot_round_trip,
            reason=f"target_premium ({target_premium}p) is not above premium ({premium}p) — no positive edge",
        )

    required_edge = Decimal(per_lot_round_trip) * min_edge_multiple
    if Decimal(gross_edge_per_lot) < required_edge:
        return _rejected(
            round_trip_cost_paise=per_lot_round_trip,
            reason=(
                f"gross edge per lot ({gross_edge_per_lot}p) < round-trip cost ({per_lot_round_trip}p) "
                f"x min_edge_multiple ({min_edge_multiple}) = {required_edge}p"
            ),
        )

    risk_per_lot = (premium - stop_premium) * lot_size
    if risk_per_lot <= 0:
        return _rejected(
            round_trip_cost_paise=per_lot_round_trip,
            reason=f"stop_premium ({stop_premium}p) is not below premium ({premium}p) — no real stop-loss risk defined",
        )

    risk_budget_paise = int(Decimal(capital) * risk_budget_pct / Decimal(100))
    lots_by_risk = risk_budget_paise // risk_per_lot
    if lots_by_risk < 1:
        return _rejected(
            round_trip_cost_paise=per_lot_round_trip,
            reason=(
                f"risk budget ({risk_budget_paise}p = {risk_budget_pct}% of capital {capital}p) is insufficient "
                f"for even 1 lot at risk_per_lot={risk_per_lot}p"
            ),
        )

    lot_cost = premium * lot_size
    lots_by_capital = capital // lot_cost if lot_cost > 0 else 0
    if lots_by_capital < 1:
        return _rejected(
            round_trip_cost_paise=per_lot_round_trip,
            reason=f"capital ({capital}p) cannot afford even 1 lot at premium={premium}p x lot_size={lot_size}",
        )

    # `lot_cost > 0` is already proven here — `lots_by_capital >= 1` above
    # required it (the `lot_cost > 0 else 0` guard on that line is what
    # would have rejected a zero `lot_cost` first).
    max_position_paise = int(Decimal(capital) * max_position_size_pct / Decimal(100))
    lots_by_position_size = max_position_paise // lot_cost
    if lots_by_position_size < 1:
        return _rejected(
            round_trip_cost_paise=per_lot_round_trip,
            reason=(
                f"max_position_size_pct ({max_position_size_pct}% of capital {capital}p = {max_position_paise}p) "
                f"is insufficient for even 1 lot at premium={premium}p x lot_size={lot_size}"
            ),
        )

    lots = min(lots_by_risk, lots_by_capital, lots_by_position_size)
    if lots < 1:
        return _rejected(
            round_trip_cost_paise=per_lot_round_trip,
            reason=(
                "sized to fewer than 1 lot after applying the risk-budget, capital-affordability, "
                "and max-position-size caps"
            ),
        )
    # AFTER the `< 1` check, never inside the `min` above: the three computed
    # caps can each legitimately reject a signal, and their reasons name the
    # constraint that bound. `max_lots` is an owner-set ceiling, not a
    # constraint discovered from the account, so it must never be the thing
    # that turns a tradeable signal into a skip — folding it into the `min`
    # would let `max_lots=0` produce a rejection whose message blames the
    # risk budget.
    if max_lots is not None:
        if max_lots < 1:
            raise ValueError(f"max_lots must be at least 1 when set, got {max_lots}")
        lots = min(lots, max_lots)

    risk_paise = Paise(risk_per_lot * lots)
    total_round_trip = costs.round_trip(
        entry_premium=premium, exit_premium=target_premium, qty=lot_size * lots, exchange=exchange, on=on
    ).total
    cost_as_pct_of_risk = (
        (Decimal(total_round_trip) / Decimal(risk_paise) * Decimal(100)) if risk_paise > 0 else Decimal(0)
    )

    return SizingResult(
        lots=lots,
        risk_paise=risk_paise,
        round_trip_cost_paise=total_round_trip,
        cost_as_pct_of_risk=cost_as_pct_of_risk,
        rejected_reason=None,
    )
