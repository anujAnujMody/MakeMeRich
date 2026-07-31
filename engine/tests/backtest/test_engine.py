"""`te.backtest.engine.run_backtest` — bar-close event loop, zero lookahead
by construction (delegates every read to `bars_asof`, adds no window logic
of its own — see `tests/data/test_asof.py` for the underlying point-in-time
guarantee this loop inherits)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pandas as pd
import pytest

from te.backtest.engine import BacktestConfig, run_backtest
from te.backtest.fills import BacktestFillEngine
from te.data.barstore import BAR_COLUMNS, BarStore
from te.domain.clock import IST
from te.domain.costs import ChargeRates, CostModel
from te.domain.geometry import AbsolutePointGeometry
from te.domain.money import Paise
from te.domain.pnl import net_pnl
from te.strategy.orb import OrbParams, OrbStrategy

INSTRUMENT = "NIFTY"
EXCHANGE = "NFO"


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


def _bar(event_ts: dt.datetime, *, o: float, h: float, low: float, c: float, v: int) -> dict[str, object]:
    return {
        "symbol": INSTRUMENT, "exchange": EXCHANGE, "event_ts": event_ts, "interval": "1m",
        "o": o, "h": h, "l": low, "c": c, "v": v, "oi": 0, "ingested_at": event_ts, "source": "test",
    }


def _open(minute: int) -> dt.datetime:
    base = dt.datetime(2026, 7, 29, 9, 15, tzinfo=IST)
    return base + dt.timedelta(minutes=minute)


@pytest.fixture
def store(tmp_path) -> BarStore:  # noqa: ANN001
    return BarStore(tmp_path)


def _config() -> BacktestConfig:
    return BacktestConfig(
        capital=Paise(30_000_00),
        risk_budget_pct=Decimal("2"),
        min_edge_multiple=Decimal("1"),
        exit_geometry=AbsolutePointGeometry(
            stop_distance=Paise(300), target_distance=Paise(200), trailing_distance=None
        ),
        max_hold=dt.timedelta(hours=6),
        hard_exit_by=dt.time(15, 20),
        lot_size=65,
    )


def test_backtest_enters_on_breakout_and_exits_on_target(store: BarStore, cost_model: CostModel) -> None:
    rows = [
        _bar(_open(0), o=100, h=105, low=95, c=100, v=1_000),
        _bar(_open(1), o=100, h=101, low=99, c=100.5, v=1_000),
        _bar(_open(2), o=100, h=101, low=99, c=100.2, v=1_000),
        _bar(_open(15), o=100, h=110, low=100, c=108, v=1_500),  # breakout, entry premium 108.00 -> 10800p
        _bar(_open(16), o=108, h=112, low=107, c=112, v=1_200),  # premium 11200p, hits target (11000p)
    ]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))

    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    # Slippage explicitly disabled: this test pins the exact entry/exit
    # premium MECHANICS (bar close -> fill price -> target barrier), so it
    # must not also absorb `BacktestFillEngine`'s default slippage floor.
    # `tests/backtest/test_fills.py` covers that default separately.
    fills = BacktestFillEngine(cost_model=cost_model, slippage_bps=Decimal(0))
    timestamps = [_open(i) for i in [1, 2, 16, 17]]

    result = run_backtest(
        store=store, instrument=INSTRUMENT, exchange=EXCHANGE, strategy=strategy, cost_model=cost_model,
        fills=fills, config=_config(), timestamps=timestamps,
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "target"
    assert trade.entry_premium == Paise(10_800)
    assert trade.exit_premium == Paise(11_200)

    qty = trade.lots * trade.lot_size
    expected_costs = cost_model.round_trip(
        entry_premium=trade.entry_premium, exit_premium=trade.exit_premium, qty=qty, exchange=EXCHANGE,
        on=trade.exit_ts.date(),
    )
    expected_net = net_pnl(trade.entry_premium, trade.exit_premium, qty, expected_costs)

    assert trade.costs == expected_costs
    assert trade.net_pnl == expected_net
    # Net must differ from gross by exactly the real cost total — never a
    # gross figure masquerading as net.
    assert int(trade.net_pnl) == int(trade.gross_pnl) - expected_costs.total


def test_backtest_only_ever_sees_bars_closed_at_or_before_as_of(store: BarStore, cost_model: CostModel) -> None:
    """Zero-lookahead sanity check: bars recorded FAR in the future (beyond
    every `as_of` the loop steps through) must never influence the result —
    if they did, the strategy would fire on the very first timestamp
    instead of skipping until the opening range/breakout bars are visible."""
    rows = [
        _bar(_open(0), o=100, h=101, low=99, c=100, v=1_000),
        _bar(_open(1), o=100, h=101, low=99, c=100.2, v=1_000),
        _bar(_open(2), o=100, h=101, low=99, c=100.1, v=1_000),
        # A dramatic future breakout bar the loop's early timestamps must
        # never be able to see.
        _bar(_open(15), o=100, h=200, low=100, c=190, v=50_000),
    ]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))

    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    fills = BacktestFillEngine(cost_model=cost_model)

    # Step through only the FIRST two timestamps — the breakout bar at
    # minute 15 must not be visible yet (its close_ts is in the future
    # relative to as_of=_open(1)/_open(2)).
    result = run_backtest(
        store=store, instrument=INSTRUMENT, exchange=EXCHANGE, strategy=strategy, cost_model=cost_model,
        fills=fills, config=_config(), timestamps=[_open(1), _open(2)],
    )

    assert result.trades == ()


def test_backtest_rejects_zero_lot_sizing_silently_as_a_skip(store: BarStore, cost_model: CostModel) -> None:
    """A signal that fires but can't be sized (e.g. capital too small)
    produces no trade, not a crash — mirrors `te.risk.sizing`'s always-
    explained-skip guarantee, just without a persisted `SkippedSignal` row
    (backtest has no DB session in this phase)."""
    rows = [
        _bar(_open(0), o=100, h=105, low=95, c=100, v=1_000),
        _bar(_open(1), o=100, h=101, low=99, c=100.5, v=1_000),
        _bar(_open(2), o=100, h=101, low=99, c=100.2, v=1_000),
        _bar(_open(15), o=100, h=110, low=100, c=108, v=1_500),
    ]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))

    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    fills = BacktestFillEngine(cost_model=cost_model)
    tiny_capital_config = BacktestConfig(
        capital=Paise(100),  # cannot afford even 1 lot at premium 108.00 x 65
        risk_budget_pct=Decimal("2"), min_edge_multiple=Decimal("1"),
        exit_geometry=AbsolutePointGeometry(
            stop_distance=Paise(300), target_distance=Paise(200), trailing_distance=None
        ),
        max_hold=dt.timedelta(hours=6), hard_exit_by=dt.time(15, 20), lot_size=65,
    )

    result = run_backtest(
        store=store, instrument=INSTRUMENT, exchange=EXCHANGE, strategy=strategy, cost_model=cost_model,
        fills=fills, config=tiny_capital_config, timestamps=[_open(1), _open(2), _open(16)],
    )

    assert result.trades == ()
