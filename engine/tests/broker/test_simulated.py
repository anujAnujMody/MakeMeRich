"""te.broker.simulated.SimulatedBroker — the paper venue. Runs every fill
through `CostModel` so paper P&L is net from day one (the plan's "one fill
path" guarantee); shared later by both paper trading and backtesting."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from te.broker.simulated import SimulatedBroker
from te.domain.costs import ChargeRates, CostModel
from te.domain.money import Paise
from te.domain.orders import OrderIntent

TS = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)


@pytest.fixture
def rates() -> ChargeRates:
    return ChargeRates(
        effective_from=dt.date(2026, 4, 1),
        verified_at=dt.date(2026, 7, 29),
        brokerage_per_executed_order_paise=Paise(2000),
        stt_sell_bps=Decimal("15.0"),
        stt_exercise_intrinsic_bps=Decimal("15.0"),
        exchange_txn_bps={"NFO": Decimal("3.553"), "BFO": Decimal("3.25")},
        sebi_bps=Decimal("0.01"),
        gst_pct=Decimal("18.0"),
        stamp_buy_bps=Decimal("0.3"),
    )


@pytest.fixture
def cost_model(rates: ChargeRates) -> CostModel:
    return CostModel(rates)


_DEFAULT_PRICE = Paise(10_000)


def _intent(*, side: str = "BUY", qty: int = 65, price: Paise = _DEFAULT_PRICE) -> OrderIntent:
    return OrderIntent(
        client_order_id="coid-1",
        symbol="NIFTY30JUN2626500CE",
        exchange="NFO",
        side=side,  # type: ignore[arg-type]
        quantity=qty,
        order_type="LIMIT",
        limit_price=price,
        ts=TS,
    )


def test_place_order_fills_immediately_at_limit_price(cost_model: CostModel) -> None:
    broker = SimulatedBroker(cost_model=cost_model, on=dt.date(2026, 7, 29))
    ack = broker.place_order(_intent())

    assert ack.client_order_id == "coid-1"
    assert ack.venue_order_id

    reports = broker.order_reports()
    assert len(reports) == 1
    assert reports[0].status == "complete"
    assert reports[0].filled_qty == 65


def test_simulated_broker_fill_is_net_of_costs(cost_model: CostModel) -> None:
    broker = SimulatedBroker(cost_model=cost_model, on=dt.date(2026, 7, 29))
    broker.place_order(_intent(side="BUY", qty=65, price=Paise(10_000)))

    fills = broker.fill_reports()
    assert len(fills) == 1
    fill = fills[0]

    gross = -(Paise(10_000) * 65)  # BUY: cash outflow
    assert fill.gross_amount_paise == gross
    assert fill.cost_breakdown.total > 0
    # Net must differ from gross by exactly the cost total — never equal to
    # gross ("net P&L consistent with CostModel, not gross").
    assert fill.net_amount_paise == gross - fill.cost_breakdown.total
    assert fill.net_amount_paise != gross


def test_sell_fill_net_amount_reduced_by_costs(cost_model: CostModel) -> None:
    broker = SimulatedBroker(cost_model=cost_model, on=dt.date(2026, 7, 29))
    broker.place_order(_intent(side="SELL", qty=65, price=Paise(12_000)))

    fill = broker.fill_reports()[0]
    gross = Paise(12_000) * 65  # SELL: cash inflow
    assert fill.gross_amount_paise == gross
    assert fill.net_amount_paise == gross - fill.cost_breakdown.total
    assert fill.net_amount_paise < gross


def test_position_reports_reflect_signed_quantity(cost_model: CostModel) -> None:
    broker = SimulatedBroker(cost_model=cost_model, on=dt.date(2026, 7, 29))
    broker.place_order(_intent(side="BUY", qty=65, price=Paise(10_000)))

    positions = broker.position_reports()
    assert len(positions) == 1
    assert positions[0].symbol == "NIFTY30JUN2626500CE"
    assert positions[0].quantity == 65


def test_deterministic_slippage_applied_in_adverse_direction(cost_model: CostModel) -> None:
    broker = SimulatedBroker(cost_model=cost_model, on=dt.date(2026, 7, 29), slippage_bps=Decimal("10"))
    broker.place_order(_intent(side="BUY", qty=65, price=Paise(10_000)))
    fill = broker.fill_reports()[0]
    # BUY slippage moves the fill price UP (worse for the buyer). Asserted
    # to the exact paise: 10 bps of Rs 100.00 is Rs 0.10 (10 paise), so the
    # fill must land at exactly 10_010 — a magnitude bug (e.g. a dropped
    # `/ _BPS_DIVISOR`, which would move a Rs 100.00 fill to Rs 1,100.00)
    # would still satisfy a bare `>` comparison.
    assert fill.fill_price == Paise(10_010)


def test_deterministic_slippage_applied_in_adverse_direction_for_a_sell(cost_model: CostModel) -> None:
    """SELL is the untested direction: "always worse for the trader" means
    the fill price must move DOWN, not up, when selling. No existing test
    constructs a SELL with non-zero `slippage_bps`, so a sign error here
    (`- adjustment` -> `+ adjustment`) previously survived the whole suite."""
    broker = SimulatedBroker(cost_model=cost_model, on=dt.date(2026, 7, 29), slippage_bps=Decimal("10"))
    broker.place_order(_intent(side="SELL", qty=65, price=Paise(12_000)))
    fill = broker.fill_reports()[0]
    assert fill.fill_price < Paise(12_000)
    assert fill.fill_price == Paise(11_988)  # 10 bps of Rs 120.00 = 12 paise, worse for a seller


def test_query_order_returns_report_by_client_order_id(cost_model: CostModel) -> None:
    broker = SimulatedBroker(cost_model=cost_model, on=dt.date(2026, 7, 29))
    broker.place_order(_intent())
    report = broker.query_order("coid-1")
    assert report is not None
    assert report.client_order_id == "coid-1"
    assert report.status == "complete"


def test_query_order_unknown_id_returns_none(cost_model: CostModel) -> None:
    broker = SimulatedBroker(cost_model=cost_model, on=dt.date(2026, 7, 29))
    assert broker.query_order("nonexistent") is None
