"""`te.backtest.fills.BacktestFillEngine` — the plan's "one fill path"
guarantee for backtest vs paper trading. The standing invariant test here
(`test_backtest_and_paper_produce_identical_fill_for_same_input`) must never
be deleted or loosened.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from te.backtest.fills import DEFAULT_SLIPPAGE_BPS, BacktestFillEngine
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
        client_order_id="coid-shared",
        symbol="NIFTY30JUN2626500CE",
        exchange="NFO",
        side=side,  # type: ignore[arg-type]
        quantity=qty,
        order_type="LIMIT",
        limit_price=price,
        ts=TS,
    )


def test_backtest_and_paper_produce_identical_fill_for_same_input(cost_model: CostModel) -> None:
    intent = _intent(side="BUY", qty=65, price=Paise(10_000))

    # Paper path. Matched explicitly to the backtest engine's default
    # slippage floor — see `DEFAULT_SLIPPAGE_BPS`.
    paper_broker = SimulatedBroker(cost_model=cost_model, on=dt.date(2026, 7, 29), slippage_bps=DEFAULT_SLIPPAGE_BPS)
    paper_broker.place_order(intent)
    paper_fill = paper_broker.fill_reports()[0]

    # Backtest path.
    backtest_fills = BacktestFillEngine(cost_model=cost_model)
    backtest_fill = backtest_fills.fill(intent)

    assert backtest_fill.cost_breakdown == paper_fill.cost_breakdown
    assert backtest_fill.fill_price == paper_fill.fill_price
    assert backtest_fill.gross_amount_paise == paper_fill.gross_amount_paise
    assert backtest_fill.net_amount_paise == paper_fill.net_amount_paise


def test_backtest_and_paper_produce_identical_fill_with_slippage(cost_model: CostModel) -> None:
    intent = _intent(side="SELL", qty=65, price=Paise(12_000))
    slippage = Decimal("10")

    paper_broker = SimulatedBroker(cost_model=cost_model, on=dt.date(2026, 7, 29), slippage_bps=slippage)
    paper_broker.place_order(intent)
    paper_fill = paper_broker.fill_reports()[0]

    backtest_fills = BacktestFillEngine(cost_model=cost_model, slippage_bps=slippage)
    backtest_fill = backtest_fills.fill(intent)

    assert backtest_fill.cost_breakdown == paper_fill.cost_breakdown
    assert backtest_fill.fill_price == paper_fill.fill_price
    assert backtest_fill.net_amount_paise == paper_fill.net_amount_paise


def test_backtest_slippage_defaults_to_a_nonzero_floor(cost_model: CostModel) -> None:
    """A backtest that models zero slippage overstates every result. The
    default used to be `Decimal(0)` and no production caller ever supplied
    anything else, so slippage was never actually exercised."""
    assert DEFAULT_SLIPPAGE_BPS > 0

    intent = _intent(side="BUY", qty=65, price=Paise(10_000))

    zero_slippage = BacktestFillEngine(cost_model=cost_model, slippage_bps=Decimal(0)).fill(intent)
    defaulted = BacktestFillEngine(cost_model=cost_model).fill(intent)

    # A BUY fills WORSE (higher) than the zero-slippage reference.
    assert defaulted.fill_price > zero_slippage.fill_price
