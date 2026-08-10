"""`te.risk.sizing.size_position` — exact boundary/arithmetic mutants not
covered by `test_sizing.py`/`test_max_lots.py`: the `<=`/`<` edges on the
cost-vs-edge and capital/position-size caps, and the two multiplication/
division sites whose exact numeric result was never pinned (only `>= 1`
sanity-checked)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from te.data.charges_loader import load_charge_rate_table
from te.domain.costs import CostBreakdown, CostModel, select_rates
from te.domain.money import Paise
from te.risk.sizing import size_position

_CHARGES_PATH = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"
ON = dt.date(2026, 7, 29)


@pytest.fixture
def cost_model() -> CostModel:
    table = load_charge_rate_table(_CHARGES_PATH)
    return CostModel(select_rates(table, ON))


class _FixedCostModel:
    """A `round_trip()` stub that returns a FIXED total regardless of its
    inputs — `size_position` only ever calls `.round_trip(...).total` on
    whatever `costs` object it is given, so this is a legitimate stand-in for
    isolating the pure `<`/`<=` comparisons below from the real (rounding-
    sensitive, qty-sensitive) `CostModel` formula, which would otherwise make
    engineering an EXACT boundary value against `min_edge_multiple` a Decimal-
    precision exercise unrelated to what these mutants actually test."""

    def __init__(self, total_paise: int) -> None:
        self._total = total_paise

    def round_trip(self, **_kwargs: object) -> CostBreakdown:
        return CostBreakdown(
            brokerage=Paise(self._total), stt=Paise(0), exchange_txn=Paise(0), sebi=Paise(0), gst=Paise(0), stamp=Paise(0)
        )


def test_zero_gross_edge_is_rejected_for_having_no_positive_edge_not_for_cost(cost_model: CostModel) -> None:
    """`gross_edge_per_lot == 0` (target exactly at premium) must be rejected
    by the FIRST gate (`<= 0`, "no positive edge"), not fall through to the
    cost-vs-edge gate. `< 0` would let exactly-zero edge slip past this check
    to the next one, which still rejects it but for the wrong stated reason —
    only reachable in a test that fixes the reason text, not merely `lots==0`."""
    premium = Paise(3_500)
    result = size_position(
        capital=Paise(2_500_000),
        risk_budget_pct=Decimal(2),
        premium=premium,
        stop_premium=Paise(2_800),
        target_premium=premium,  # target == premium -> gross_edge_per_lot == 0 exactly
        lot_size=65,
        costs=cost_model,
        exchange="NFO",
        on=ON,
        min_edge_multiple=Decimal("1.5"),
    )

    assert result.lots == 0
    assert result.rejected_reason is not None
    assert "no positive edge" in result.rejected_reason


def test_gross_edge_exactly_equal_to_required_edge_is_not_rejected(cost_model: CostModel) -> None:
    """The cost-vs-edge gate is `gross_edge_per_lot < required_edge` —
    strictly less than. At EXACT equality the trade must be accepted, not
    rejected; `<=` would reject a signal that pays for itself exactly
    `min_edge_multiple` times over."""
    costs = _FixedCostModel(total_paise=1_000)  # per-lot round-trip cost, fixed
    result = size_position(
        capital=Paise(1_000_000_00),  # generous — risk/capital/position caps never bind
        risk_budget_pct=Decimal(50),
        premium=Paise(5_000),
        stop_premium=Paise(4_000),  # risk_per_lot = 1_000 x 1 = 1_000p
        target_premium=Paise(7_000),  # gross_edge_per_lot = 2_000 x 1 = 2_000p
        lot_size=1,
        costs=costs,  # type: ignore[arg-type]
        exchange="NFO",
        on=ON,
        min_edge_multiple=Decimal(2),  # required_edge = 1_000 x 2 = 2_000p == gross_edge_per_lot exactly
    )

    assert result.rejected_reason is None
    assert result.lots >= 1


def test_capital_affordability_is_the_binding_cap_when_it_is_the_smallest(cost_model: CostModel) -> None:
    """Mirrors `test_sizing.py::test_risk_budget_is_the_binding_cap_...` but
    for `lots_by_capital = capital // lot_cost`, never separately pinned —
    every other test has capital sitting ABOVE at least one other cap.
    `lots_by_capital` lands at EXACTLY 1 here, which also pins the `< 1`
    boundary immediately below it (`<=1` would wrongly reject this)."""
    premium = Paise(3_500)  # Rs 35
    lot_size = 65
    lot_cost = int(premium) * lot_size  # 227,500p
    capital = Paise(2_500_00)  # Rs 2,500 -> lots_by_capital = 250_000 // 227_500 = 1
    assert int(capital) // lot_cost == 1, "the premise of this test no longer holds — re-derive the numbers"

    result = size_position(
        capital=capital,
        risk_budget_pct=Decimal(100),  # generous — never binds
        premium=premium,
        stop_premium=Paise(3_495),  # tiny risk_per_lot -> risk cap never binds
        target_premium=Paise(4_500),
        lot_size=lot_size,
        costs=cost_model,
        exchange="NFO",
        on=ON,
        min_edge_multiple=Decimal("0.01"),
        max_position_size_pct=Decimal(500),  # max_position_paise = 12,500p -> lots_by_position_size = 5, not binding
    )

    assert result.rejected_reason is None
    assert result.lots == 1


def test_max_position_size_lots_exactly_one_is_not_rejected(cost_model: CostModel) -> None:
    """`lots_by_position_size == 1` exactly, with capital/risk generous
    enough that neither of the other two caps binds — pins the `< 1` boundary
    on `lots_by_position_size` (`<=1` would wrongly reject this)."""
    premium = Paise(3_500)  # Rs 35
    lot_size = 65
    lot_cost = int(premium) * lot_size  # 227,500p
    capital = Paise(2_500_000)  # Rs 25,000 -> lots_by_capital = 2_500_000 // 227_500 = 10, not binding
    max_position_size_pct = Decimal(10)  # max_position_paise = 250,000p -> lots_by_position_size = 1
    assert (int(capital) * max_position_size_pct / 100) // lot_cost == 1, (
        "the premise of this test no longer holds — re-derive the numbers"
    )

    result = size_position(
        capital=capital,
        risk_budget_pct=Decimal(100),  # generous — never binds
        premium=premium,
        stop_premium=Paise(3_495),  # tiny risk_per_lot -> risk cap never binds
        target_premium=Paise(4_500),
        lot_size=lot_size,
        costs=cost_model,
        exchange="NFO",
        on=ON,
        min_edge_multiple=Decimal("0.01"),
        max_position_size_pct=max_position_size_pct,
    )

    assert result.rejected_reason is None
    assert result.lots == 1


def test_total_round_trip_cost_uses_lot_size_times_lots_as_quantity(cost_model: CostModel) -> None:
    """The FINAL round-trip cost (on the accepted path) must be quoted at
    `qty=lot_size * lots` — the real total quantity actually being bought —
    not some other combination. Pinned against an independent call to the
    same real `CostModel` with that exact `qty`."""
    premium = Paise(3_500)
    target = Paise(4_500)
    lot_size = 65

    result = size_position(
        capital=Paise(50_000_00),  # Rs 50,000 — generous, lots_by_risk is the binding (and > 1) cap
        risk_budget_pct=Decimal(10),
        premium=premium,
        stop_premium=Paise(3_400),
        target_premium=target,
        lot_size=lot_size,
        costs=cost_model,
        exchange="NFO",
        on=ON,
        min_edge_multiple=Decimal("1.2"),
    )

    assert result.rejected_reason is None
    assert result.lots > 1, "need >1 lot for lot_size*lots to differ from every other plausible qty"
    expected_total = cost_model.round_trip(
        entry_premium=premium, exit_premium=target, qty=lot_size * result.lots, exchange="NFO", on=ON
    ).total
    assert result.round_trip_cost_paise == expected_total


def test_cost_as_pct_of_risk_matches_the_exact_formula(cost_model: CostModel) -> None:
    """`cost_as_pct_of_risk = total_round_trip / risk_paise x 100` — pinned
    against the same formula computed independently in the test, so a
    `/ -> *` or `* -> //` mutation on either operator produces a visibly
    different number."""
    premium = Paise(3_500)
    target = Paise(4_500)
    lot_size = 65

    result = size_position(
        capital=Paise(50_000_00),
        risk_budget_pct=Decimal(10),
        premium=premium,
        stop_premium=Paise(3_400),
        target_premium=target,
        lot_size=lot_size,
        costs=cost_model,
        exchange="NFO",
        on=ON,
        min_edge_multiple=Decimal("1.2"),
    )

    assert result.rejected_reason is None
    expected_pct = Decimal(int(result.round_trip_cost_paise)) / Decimal(int(result.risk_paise)) * Decimal(100)
    assert result.cost_as_pct_of_risk == expected_pct
