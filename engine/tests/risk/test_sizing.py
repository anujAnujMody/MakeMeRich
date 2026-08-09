"""`te.risk.sizing.size_position()` — the exact bug class from the old
engine ("a sizing bug meant it had never placed a single trade") closed
structurally: any `lots=0` result MUST carry a real `rejected_reason`."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from te.data.charges_loader import load_charge_rate_table
from te.domain.costs import CostModel, select_rates
from te.domain.money import Paise
from te.risk.sizing import SizingResult, size_position

_CHARGES_PATH = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"
ON = dt.date(2026, 7, 29)


@pytest.fixture
def cost_model() -> CostModel:
    table = load_charge_rate_table(_CHARGES_PATH)
    return CostModel(select_rates(table, ON))


def test_sizing_rejects_when_cost_exceeds_edge(cost_model: CostModel) -> None:
    """₹20 premium -> ₹24 target, SENSEX lot 20 (post-Jan-2026 lot size).
    Gross edge is real but thin; with a min_edge_multiple that demands the
    edge be worth at least double the real round-trip cost, this must be
    rejected — proving the "₹20-premium trap" gate actually fires using the
    real CostModel, not a hand-waved number."""
    premium = Paise(2_000)
    target = Paise(2_400)
    lot_size = 20

    per_lot_cost = cost_model.round_trip(
        entry_premium=premium, exit_premium=target, qty=lot_size, exchange="BFO", on=ON
    ).total
    gross_edge = (target - premium) * lot_size
    assert gross_edge == 8_000  # sanity: matches the plan's worked numbers
    # Pick a multiple that this specific edge/cost pair genuinely fails —
    # proves the gate is a real inequality over real numbers, not tautological.
    min_edge_multiple = Decimal(gross_edge) / Decimal(per_lot_cost) + Decimal("0.5")

    result = size_position(
        capital=Paise(2_500_000),  # ₹25,000
        risk_budget_pct=Decimal(2),
        premium=premium,
        stop_premium=Paise(1_600),
        target_premium=target,
        lot_size=lot_size,
        costs=cost_model,
        exchange="BFO",
        on=ON,
        min_edge_multiple=min_edge_multiple,
    )

    assert result.lots == 0
    assert result.rejected_reason is not None
    assert "round-trip cost" in result.rejected_reason


def test_sizing_succeeds_for_a_realistic_near_the_money_trade(cost_model: CostModel) -> None:
    """~1-1.5% OTM band the plan favors: ~₹2,000-4,000/lot, e.g. NIFTY
    24,500 CE @ ₹35 -> ₹2,282/lot on a 65-lot."""
    result = size_position(
        capital=Paise(2_500_000),  # ₹25,000
        risk_budget_pct=Decimal(2),
        premium=Paise(3_500),  # ₹35
        stop_premium=Paise(2_800),  # ₹28 stop
        target_premium=Paise(4_500),  # ₹45 target
        lot_size=65,
        costs=cost_model,
        exchange="NFO",
        on=ON,
        min_edge_multiple=Decimal("1.5"),
    )

    assert result.lots >= 1
    assert result.rejected_reason is None
    assert result.risk_paise > 0
    assert result.round_trip_cost_paise > 0


@pytest.mark.parametrize(
    "kwargs",
    [
        # target not above premium -> no positive edge at all.
        {"premium": Paise(3_500), "stop_premium": Paise(2_800), "target_premium": Paise(3_500)},
        # stop not below premium -> no real stop-loss risk defined.
        {"premium": Paise(3_500), "stop_premium": Paise(3_500), "target_premium": Paise(4_500)},
        # capital far too small to buy even one lot.
        {
            "premium": Paise(3_500),
            "stop_premium": Paise(2_800),
            "target_premium": Paise(4_500),
            "capital": Paise(1_000),
        },
        # risk budget (tiny pct) too small to afford even one lot's worth of risk.
        {
            "premium": Paise(3_500),
            "stop_premium": Paise(2_800),
            "target_premium": Paise(4_500),
            "risk_budget_pct": Decimal("0.001"),
        },
    ],
)
def test_sizing_never_returns_zero_lots_silently(cost_model: CostModel, kwargs: dict[str, object]) -> None:
    """For ANY input that results in lots=0, rejected_reason must be set —
    the single most important test in this phase."""
    defaults: dict[str, object] = {
        "capital": Paise(2_500_000),
        "risk_budget_pct": Decimal(2),
        "lot_size": 65,
        "costs": cost_model,
        "exchange": "NFO",
        "on": ON,
        "min_edge_multiple": Decimal("1.5"),
    }
    defaults.update(kwargs)

    result = size_position(**defaults)  # type: ignore[arg-type]

    if result.lots == 0:
        assert result.rejected_reason is not None
        assert result.rejected_reason != ""
    else:
        assert result.rejected_reason is None


def test_risk_budget_is_the_binding_cap_when_it_is_the_smallest(cost_model: CostModel) -> None:
    """`lots = min(lots_by_risk, lots_by_capital, lots_by_position_size)` —
    every other test in this module/`test_max_lots.py` has `lots_by_risk`
    sitting ABOVE at least one of the other two caps, so the risk budget is
    only ever exercised as an on/off floor (`lots_by_risk < 1` rejects), never
    as the thing that actually sets the size. A generous capital/position-
    size ceiling and a tight risk budget here makes `lots_by_risk` strictly
    the smallest, so dropping it from the `min()` would silently change the
    result."""
    capital = Paise(50_000_00)  # Rs 50,000 — affordability/position-size caps stay generous
    risk_budget_pct = Decimal("1")  # Rs 500 risk budget — deliberately tight
    premium = Paise(3_500)  # Rs 35
    stop_premium = Paise(3_450)  # Rs 0.50 stop distance -> risk_per_lot = 0.50 x 65 = Rs 32.50/lot
    lot_size = 65

    result = size_position(
        capital=capital,
        risk_budget_pct=risk_budget_pct,
        premium=premium,
        stop_premium=stop_premium,
        target_premium=Paise(4_500),
        lot_size=lot_size,
        costs=cost_model,
        exchange="NFO",
        on=ON,
        min_edge_multiple=Decimal("1.2"),
        max_position_size_pct=Decimal(100),
    )

    risk_per_lot = (int(premium) - int(stop_premium)) * lot_size
    risk_budget_paise = int(Decimal(int(capital)) * risk_budget_pct / Decimal(100))
    lots_by_risk = risk_budget_paise // risk_per_lot
    lots_by_capital = int(capital) // (int(premium) * lot_size)
    assert lots_by_risk < lots_by_capital, "the premise of this test no longer holds — re-derive the numbers"

    assert result.rejected_reason is None
    assert result.lots == lots_by_risk
    assert result.lots == risk_budget_paise // risk_per_lot


def test_max_position_size_pct_caps_lots_below_the_risk_and_capital_caps(cost_model: CostModel) -> None:
    """A position can be well within its risk budget yet still tie up an
    outsized share of capital in one bet when the stop is close to premium
    — `max_position_size_pct` is a separate notional cap from
    `risk_budget_pct`. A generous risk budget (10%) would otherwise size
    many more lots than a tight 5%-of-capital position cap should allow."""
    shared: dict[str, object] = {
        "capital": Paise(2_500_000),  # ₹25,000
        "risk_budget_pct": Decimal(10),
        "premium": Paise(3_500),  # ₹35
        "stop_premium": Paise(3_400),  # tight stop -> risk budget alone would size many lots
        "target_premium": Paise(4_500),
        "lot_size": 65,
        "costs": cost_model,
        "exchange": "NFO",
        "on": ON,
        "min_edge_multiple": Decimal("1.5"),
    }

    uncapped = size_position(**shared, max_position_size_pct=Decimal(100))  # type: ignore[arg-type]
    capped = size_position(**shared, max_position_size_pct=Decimal(30))  # type: ignore[arg-type]

    assert uncapped.lots > capped.lots
    assert capped.lots >= 1
    # premium=₹35 x lot_size=65 = ₹2,275/lot notional; ₹25,000 x 30% = ₹7,500 caps it at 3 lots,
    # well below the risk-budget/capital-affordability answer (10 lots).
    notional_per_lot = 3_500 * 65
    max_notional = int(2_500_000 * Decimal(30) / 100)
    assert capped.lots == max_notional // notional_per_lot


def test_max_position_size_pct_rejects_with_a_real_reason_when_too_tight(cost_model: CostModel) -> None:
    result = size_position(
        capital=Paise(2_500_000),
        risk_budget_pct=Decimal(10),
        premium=Paise(3_500),
        stop_premium=Paise(3_400),
        target_premium=Paise(4_500),
        lot_size=65,
        costs=cost_model,
        exchange="NFO",
        on=ON,
        min_edge_multiple=Decimal("1.5"),
        max_position_size_pct=Decimal("0.1"),  # far too tight for even 1 lot at ~₹2,275
    )

    assert result.lots == 0
    assert result.rejected_reason is not None
    assert "max_position_size_pct" in result.rejected_reason


def test_max_position_size_pct_defaults_to_100_matching_pre_existing_behaviour(cost_model: CostModel) -> None:
    shared: dict[str, object] = {
        "capital": Paise(2_500_000),
        "risk_budget_pct": Decimal(2),
        "premium": Paise(3_500),
        "stop_premium": Paise(2_800),
        "target_premium": Paise(4_500),
        "lot_size": 65,
        "costs": cost_model,
        "exchange": "NFO",
        "on": ON,
        "min_edge_multiple": Decimal("1.5"),
    }
    without_param = size_position(**shared)  # type: ignore[arg-type]
    with_explicit_100 = size_position(**shared, max_position_size_pct=Decimal(100))  # type: ignore[arg-type]
    assert without_param == with_explicit_100


def test_sizing_result_constructor_rejects_zero_lots_without_reason() -> None:
    """The invariant is enforced at the type's own constructor, not just by
    convention in `size_position()` — a caller can't accidentally construct
    a lying `SizingResult` either."""
    with pytest.raises(ValueError, match="rejected_reason"):
        SizingResult(
            lots=0,
            risk_paise=Paise(0),
            round_trip_cost_paise=Paise(0),
            cost_as_pct_of_risk=Decimal(0),
            rejected_reason=None,
        )


def test_sizing_result_constructor_rejects_nonzero_lots_with_reason() -> None:
    with pytest.raises(ValueError, match="rejected_reason"):
        SizingResult(
            lots=1,
            risk_paise=Paise(1_000),
            round_trip_cost_paise=Paise(100),
            cost_as_pct_of_risk=Decimal(10),
            rejected_reason="should not be set",
        )
