"""`te.engine.cycle` — the decision loop: fetch -> analyze -> risk -> decide
-> act, wired against `te.broker.simulated.SimulatedBroker` for paper mode.
Proves the plan's two structural guarantees end to end: every skip
(including a zero-lots sizing rejection) persists a `SkippedSignal` with a
real reason, and a closed trade's P&L is always net of the real
`CostModel`."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from te.broker.simulated import SimulatedBroker
from te.data.barstore import BAR_COLUMNS, BarStore
from te.data.charges_loader import load_charge_rate_table
from te.domain.clock import IST
from te.domain.costs import CostModel, select_rates
from te.domain.geometry import AbsolutePointGeometry, PremiumPercentGeometry
from te.domain.money import Paise
from te.engine.cycle import CycleConfig, InstrumentConfig, run_entry_cycle, run_exit_cycle
from te.engine.state import get_last_cycle_pipeline
from te.execution.manager import ExecutionManager
from te.execution.store import OrderEventStore
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, OpenPositionRow, SkippedSignalRow, TradeRow
from te.risk.killswitch import reset_in_process_cache as reset_killswitch_cache
from te.risk.killswitch import throttle as throttle_killswitch
from te.risk.killswitch import trip as trip_killswitch
from te.risk.limits import RiskLimitsConfig

_CHARGES_PATH = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"
INSTRUMENT = "NIFTY30JUN2626500CE"
EXCHANGE = "NFO"
ON = dt.date(2026, 7, 29)


class _NoLimiter:
    def acquire(self, n: int = 1) -> None:
        pass


def _bar(event_ts: dt.datetime, *, o: float, h: float, low: float, c: float, v: int) -> dict[str, object]:
    return {
        "symbol": INSTRUMENT,
        "exchange": EXCHANGE,
        "event_ts": event_ts,
        "interval": "1m",
        "o": o,
        "h": h,
        "l": low,
        "c": c,
        "v": v,
        "oi": 0,
        "ingested_at": event_ts,
        "source": "test",
    }


def _open(minute: int) -> dt.datetime:
    base = dt.datetime(2026, 7, 29, 9, 15, tzinfo=IST)
    return base + dt.timedelta(minutes=minute)


@pytest.fixture
def cost_model() -> CostModel:
    table = load_charge_rate_table(_CHARGES_PATH)
    return CostModel(select_rates(table, ON))


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'cycle_test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def execution(session_factory, cost_model: CostModel):  # noqa: ANN001, ANN201
    broker = SimulatedBroker(cost_model=cost_model, on=ON)
    store = OrderEventStore(session_factory)
    manager = ExecutionManager(session_factory, store, broker, _NoLimiter())
    return manager


def _config(**overrides: object) -> CycleConfig:
    defaults: dict[str, object] = {
        "mode": "paper",
        "strategy_name": "orb",
        "instruments": (INSTRUMENT,),
        "exchange": EXCHANGE,
        "lot_size": 65,
        "capital": Paise(2_500_000),
        "risk_budget_pct": Decimal(2),
        "min_edge_multiple": Decimal("1.2"),
        "exit_geometry": AbsolutePointGeometry(
            stop_distance=Paise(700), target_distance=Paise(1_500), trailing_distance=Paise(300)
        ),
        "max_hold": dt.timedelta(hours=3),
        "hard_exit_by": dt.time(15, 20),
        "risk_limits": RiskLimitsConfig(
            max_daily_loss_paise=Paise(10_000_00), max_concurrent_positions=5, max_trades_per_day=20
        ),
    }
    defaults.update(overrides)
    return CycleConfig(**defaults)  # type: ignore[arg-type]


def _breakout_store(tmp_path: Path) -> BarStore:
    store = BarStore(tmp_path / "bars")
    rows = [
        _bar(_open(0), o=30, h=32, low=28, c=30, v=1_000),
        _bar(_open(1), o=30, h=31, low=29, c=30.2, v=1_000),
        _bar(_open(2), o=30, h=31, low=29, c=30.1, v=1_000),
        _bar(_open(60), o=30, h=38, low=30, c=36, v=2_000),  # confirmed upside breakout, close=36 -> premium 3600p
    ]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))
    return store


def _no_signal_store(tmp_path: Path) -> BarStore:
    store = BarStore(tmp_path / "bars_empty")
    return store


def test_entry_cycle_opens_a_position_with_an_exit_plan_on_confirmed_breakout(
    session_factory,
    execution,
    cost_model: CostModel,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    store = _breakout_store(tmp_path)
    config = _config()
    as_of = _open(61)

    run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=as_of,
    )

    with session_factory() as session:
        rows = session.query(OpenPositionRow).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.closed_at is None
        assert row.stop_paise > 0
        assert row.target_paise > row.entry_premium_paise
        assert row.trailing_distance_paise == 300


def test_entry_cycle_records_real_pipeline_timing_when_a_trade_fires(
    session_factory,
    execution,
    cost_model: CostModel,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    """Regression for the dashboard's "Current cycle" strip, found live on
    2026-07-31: `pipeline` was a permanent `[]` stub because nothing ever
    recorded per-cycle stage timing. Every stage should have genuinely run
    (and be marked `reached`) when a signal actually gets all the way to a
    real order."""
    store = _breakout_store(tmp_path)
    config = _config()
    as_of = _open(61)

    cycle_id = run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=as_of,
    )

    with session_factory() as session:
        pipeline = get_last_cycle_pipeline(session)
    assert pipeline is not None
    assert pipeline.cycle_id == cycle_id
    for key in ("fetch", "analyze", "risk", "decide", "act"):
        stage = pipeline.stages[key]
        assert stage.reached is True, f"{key} should have reached on a real trade"
        assert stage.elapsed_ms >= 0


def test_entry_cycle_leaves_decide_and_act_unreached_when_every_instrument_skips(
    session_factory,
    execution,
    cost_model: CostModel,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    """No signal ever fires here (`_no_signal_store` has zero bars), so
    `decide`/`act` genuinely never ran this cycle — the pipeline must say
    so honestly rather than mark every stage `done`."""
    store = _no_signal_store(tmp_path)
    config = _config()
    as_of = _open(61)

    run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=as_of,
    )

    with session_factory() as session:
        pipeline = get_last_cycle_pipeline(session)
    assert pipeline is not None
    assert pipeline.stages["fetch"].reached is True
    assert pipeline.stages["analyze"].reached is True
    assert pipeline.stages["risk"].reached is True
    assert pipeline.stages["decide"].reached is False
    assert pipeline.stages["act"].reached is False


def test_entry_cycle_pipeline_shows_only_risk_reached_when_portfolio_halted(
    session_factory,
    execution,
    cost_model: CostModel,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    """A portfolio-level halt short-circuits BEFORE the per-instrument loop
    (see `run_entry_cycle`'s docstring) — `fetch`/`analyze`/`decide`/`act`
    never ran this cycle at all, only the portfolio risk check did."""
    store = _breakout_store(tmp_path)
    config = _config()
    as_of = _open(61)

    try:
        with session_factory() as session:
            trip_killswitch(session, "test halt")
            session.commit()

        run_entry_cycle(
            session_factory=session_factory,
            store=store,
            execution=execution,
            cost_model=cost_model,
            config=config,
            as_of=as_of,
        )

        with session_factory() as session:
            pipeline = get_last_cycle_pipeline(session)
        assert pipeline is not None
        assert pipeline.stages["risk"].reached is True
        assert pipeline.stages["fetch"].reached is False
        assert pipeline.stages["analyze"].reached is False
        assert pipeline.stages["decide"].reached is False
        assert pipeline.stages["act"].reached is False
    finally:
        # `trip()` sets the in-process kill-switch flag as a MODULE-level
        # global (see `te.risk.killswitch`'s docstring) — it survives past
        # this test's own DB teardown and would silently halt every later
        # test in this same pytest process without this reset.
        reset_killswitch_cache()


def test_entry_cycle_sizes_two_instruments_independently_across_exchanges(
    session_factory,
    execution,
    cost_model: CostModel,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    """Proves the plan's Tier 1 multi-instrument fix: `CycleConfig.
    instrument_configs` lets one cycle trade instruments on DIFFERENT
    exchanges with DIFFERENT lot sizes, each sized/recorded with its OWN
    values — never the other instrument's, and never a shared single
    `config.exchange`/`config.lot_size` (today's single-instrument
    behaviour, which this test's sibling above still covers unchanged)."""
    second_instrument = "BANKNIFTY30JUL2652000CE"
    second_exchange = "BFO"
    store = _breakout_store(tmp_path)
    # Same breakout shape as `_breakout_store`, for the second symbol/exchange.
    rows = [
        _bar(_open(0), o=30, h=32, low=28, c=30, v=1_000),
        _bar(_open(1), o=30, h=31, low=29, c=30.2, v=1_000),
        _bar(_open(2), o=30, h=31, low=29, c=30.1, v=1_000),
        _bar(_open(60), o=30, h=38, low=30, c=36, v=2_000),
    ]
    for row in rows:
        row = dict(row)
        row["symbol"] = second_instrument
        row["exchange"] = second_exchange
        store.append(pd.DataFrame([row], columns=list(BAR_COLUMNS)))

    config = _config(
        instruments=(),
        instrument_configs=(
            InstrumentConfig(symbol=INSTRUMENT, exchange=EXCHANGE, lot_size=65),
            InstrumentConfig(symbol=second_instrument, exchange=second_exchange, lot_size=30),
        ),
    )
    as_of = _open(61)

    run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=as_of,
    )

    with session_factory() as session:
        rows_by_symbol = {r.symbol: r for r in session.query(OpenPositionRow).all()}

    assert set(rows_by_symbol) == {INSTRUMENT, second_instrument}

    first, second = rows_by_symbol[INSTRUMENT], rows_by_symbol[second_instrument]
    assert first.exchange == EXCHANGE
    assert first.lot_size == 65
    assert second.exchange == second_exchange
    assert second.lot_size == 30


def test_skipped_signal_persisted_with_real_reason(
    session_factory,
    execution,
    cost_model: CostModel,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    store = _no_signal_store(tmp_path)
    config = _config()
    as_of = _open(1)

    run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=as_of,
    )

    with session_factory() as session:
        skips = session.query(SkippedSignalRow).all()
        assert len(skips) == 1
        assert skips[0].reason  # non-empty, real
        assert "opening range" in skips[0].reason


def test_a_daily_loss_breach_is_caught_even_when_no_instrument_has_a_signal(
    session_factory,
    execution,
    cost_model: CostModel,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    """Regression: the portfolio-level daily-loss/drawdown check used to
    live inside the per-instrument "signal fired" branch, so on a day with
    zero signals (the common case — most cycles skip on "opening range not
    yet formed" or similar) an ongoing breach could go undetected
    indefinitely. It must now run every cycle regardless of whether any
    instrument's rule actually fires."""
    from te.persistence.repos.paper_trading import insert_trade

    store = _no_signal_store(tmp_path)  # no bars at all -> no instrument can ever fire a signal
    config = _config(
        instruments=("NIFTY", "BANKNIFTY"),
        risk_limits=RiskLimitsConfig(
            max_daily_loss_paise=Paise(4_000_00), max_concurrent_positions=5, max_trades_per_day=20
        ),
    )
    as_of = _open(1)

    with session_factory() as session:
        insert_trade(
            session,
            client_order_id="c-already-closed",
            symbol="NIFTY30JUN2626500CE",
            exchange=EXCHANGE,
            strategy="orb",
            direction="long_call",
            lots=1,
            lot_size=65,
            entry_premium=Paise(3_000),
            exit_premium=Paise(1_000),
            gross_pnl=Paise(-5_000_00),
            costs=Paise(0),
            net_pnl=Paise(-5_000_00),  # already breaches the 4,000-rupee daily loss limit
            exit_reason="stop",
            opened_at=as_of - dt.timedelta(hours=1),
            closed_at=as_of,
        )
        session.commit()

    run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=as_of,
    )

    with session_factory() as session:
        skips = session.query(SkippedSignalRow).all()
        assert {s.instrument for s in skips} == {"NIFTY", "BANKNIFTY"}
        assert all("daily loss limit" in s.reason for s in skips)
        from te.execution.halt import is_halted

        assert is_halted(session) is True


def test_skipped_signal_persisted_when_sizing_rejects_zero_lots(
    session_factory,
    execution,
    cost_model: CostModel,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    """A traded ORB verdict whose sizing rejects (lots=0) must STILL
    persist a SkippedSignal with the real rejection reason — the exact bug
    class the plan calls out."""
    store = _breakout_store(tmp_path)
    # min_edge_multiple absurdly high -> guaranteed cost-vs-edge rejection.
    config = _config(min_edge_multiple=Decimal(1000))
    as_of = _open(61)

    run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=as_of,
    )

    with session_factory() as session:
        assert session.query(OpenPositionRow).count() == 0
        skips = session.query(SkippedSignalRow).all()
        assert len(skips) == 1
        assert "round-trip cost" in skips[0].reason


def test_paper_trade_pnl_is_net(
    session_factory,
    execution,
    cost_model: CostModel,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    """A full open -> exit cycle through SimulatedBroker: the trade's
    `net_pnl_paise` must equal `gross - costs` computed via the real
    CostModel, and no exposed field anywhere is a bare gross `pnl`."""
    store = _breakout_store(tmp_path)
    config = _config()
    entry_at = _open(61)

    run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=entry_at,
    )

    with session_factory() as session:
        open_row = session.query(OpenPositionRow).one()
        entry_premium = open_row.entry_premium_paise
        target = open_row.target_paise
        opened_stop_paise = open_row.stop_paise
        opened_target_paise = open_row.target_paise

    exit_at = entry_at + dt.timedelta(minutes=5)
    closed = run_exit_cycle(
        session_factory=session_factory,
        execution=execution,
        cost_model=cost_model,
        current_premium=lambda row: Paise(target),  # hits target immediately
        as_of=exit_at,
    )
    assert len(closed) == 1

    with session_factory() as session:
        assert session.query(OpenPositionRow).filter(OpenPositionRow.closed_at.is_(None)).count() == 0
        trade = session.query(TradeRow).one()

    qty = trade.lots * trade.lot_size
    costs = cost_model.round_trip(
        entry_premium=Paise(entry_premium), exit_premium=Paise(target), qty=qty, exchange=EXCHANGE, on=exit_at.date()
    )
    expected_gross = (target - entry_premium) * qty
    expected_net = expected_gross - costs.total

    assert trade.gross_pnl_paise == expected_gross
    assert trade.costs_paise == costs.total
    assert trade.net_pnl_paise == expected_net
    assert trade.net_pnl_paise == trade.gross_pnl_paise - trade.costs_paise
    assert trade.exit_reason == "target"

    # No bare `pnl` column anywhere on the TradeRow model.
    assert not hasattr(trade, "pnl")

    # The stop/target the position was OPENED with survive onto the closed
    # trade. They live on `OpenPositionRow`, which is gone once the position
    # closes, so without these columns a post-hoc review of "what were we
    # actually risking?" is unanswerable from the trade record alone.
    assert trade.stop_paise == opened_stop_paise
    assert trade.target_paise == opened_target_paise


def test_square_off_closes_position_immediately_regardless_of_exit_conditions(
    session_factory,
    execution,
    cost_model: CostModel,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    """Manual square-off (the plan's Tier 2 fix — `/api/positions/squareoff`
    used to always return `success=False`, honestly, since nothing was
    wired to actually close a position) must close even when NO stop/
    target/trailing/time condition has fired — that's the whole point of a
    manual override."""
    from te.engine.cycle import square_off_position

    store = _breakout_store(tmp_path)
    config = _config()
    entry_at = _open(61)

    run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=entry_at,
    )

    with session_factory() as session:
        open_row = session.query(OpenPositionRow).one()
        entry_premium = open_row.entry_premium_paise
        # A premium that hits NEITHER the stop NOR the target — proves this
        # closes independent of `evaluate_position`'s own condition check.
        mid_premium = entry_premium + 10

    square_off_at = entry_at + dt.timedelta(minutes=2)
    closed_id = square_off_position(
        session_factory=session_factory,
        execution=execution,
        cost_model=cost_model,
        symbol=INSTRUMENT,
        exchange=EXCHANGE,
        current_premium=Paise(mid_premium),
        as_of=square_off_at,
    )

    assert closed_id is not None
    with session_factory() as session:
        assert session.query(OpenPositionRow).filter(OpenPositionRow.closed_at.is_(None)).count() == 0
        trade = session.query(TradeRow).one()
    assert trade.exit_reason == "manual"
    assert trade.exit_premium_paise == mid_premium


def test_square_off_returns_none_when_no_open_position_matches(
    session_factory,
    execution,
    cost_model: CostModel,
) -> None:
    from te.engine.cycle import square_off_position

    result = square_off_position(
        session_factory=session_factory,
        execution=execution,
        cost_model=cost_model,
        symbol="NONEXISTENT",
        exchange=EXCHANGE,
        current_premium=Paise(1000),
        as_of=_open(0),
    )
    assert result is None


def test_throttle_multiplier_reaches_sizing(
    session_factory,
    execution,
    cost_model: CostModel,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    """A Phase 7 throttle (`te.risk.killswitch.throttle()`) must reduce the
    effective lot count `run_entry_cycle` actually opens a position with,
    vs an identical run with no throttle set — without changing
    `size_position()`'s own signature/behaviour (see
    `tests/risk/test_sizing.py` for proof that is unaffected)."""
    store = _breakout_store(tmp_path)
    # Large enough capital/risk budget that the baseline (untouched) sizing
    # comes out to more than 1 lot, so a 0.5x throttle multiplier is a real,
    # observable reduction rather than a reject-to-zero.
    config = _config(capital=Paise(10_000_000))
    as_of = _open(61)

    run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=as_of,
    )
    with session_factory() as session:
        baseline_row = session.query(OpenPositionRow).one()
    baseline_lots = baseline_row.lots
    assert baseline_lots > 1  # otherwise this test can't observe a reduction

    throttled_engine = make_engine(f"sqlite:///{tmp_path / 'throttled.db'}")
    Base.metadata.create_all(throttled_engine)
    throttled_session_factory = make_session_factory(throttled_engine)
    throttled_broker = SimulatedBroker(cost_model=cost_model, on=ON)
    throttled_execution = ExecutionManager(
        throttled_session_factory, OrderEventStore(throttled_session_factory), throttled_broker, _NoLimiter()
    )
    with throttled_session_factory() as session:
        throttle_killswitch(session, "Tier 0 slippage monitor")
        session.commit()

    run_entry_cycle(
        session_factory=throttled_session_factory,
        store=store,
        execution=throttled_execution,
        cost_model=cost_model,
        config=config,
        as_of=as_of,
    )
    with throttled_session_factory() as session:
        throttled_row = session.query(OpenPositionRow).one()
    assert throttled_row.lots < baseline_lots
    assert throttled_row.lots > 0


def test_exit_cycle_skips_the_db_write_when_the_trailing_stop_did_not_ratchet(
    session_factory,  # noqa: ANN001
    execution,  # noqa: ANN001
    cost_model: CostModel,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The no-exit branch runs once per open position per cycle; the stop
    only ratchets when price makes a new favourable extreme, so the common
    case must not issue an UPDATE at all. Proven by counting calls to
    `update_trailing_stop` — a flat mark cannot move the stop."""
    import te.engine.cycle as cycle_module

    store = _breakout_store(tmp_path)
    entry_at = _open(61)
    run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=_config(),
        as_of=entry_at,
    )

    with session_factory() as session:
        open_row = session.query(OpenPositionRow).one()
        entry_premium = open_row.entry_premium_paise
        stop_before = open_row.current_stop_paise

    calls: list[int] = []
    real_update = cycle_module.update_trailing_stop

    def _counting_update(session, row, new_stop):  # noqa: ANN001, ANN202
        calls.append(int(new_stop))
        return real_update(session, row, new_stop)

    monkeypatch.setattr(cycle_module, "update_trailing_stop", _counting_update)

    def _cycle(mark: int, minute: int) -> list[str]:
        return run_exit_cycle(
            session_factory=session_factory,
            execution=execution,
            cost_model=cost_model,
            current_premium=lambda row: Paise(mark),
            as_of=entry_at + dt.timedelta(minutes=minute),
        )

    # A mark at the ENTRY price must NOT ratchet: the trail activates at
    # `entry + trailing_distance` (3_600 + 300 = 3_900), so below that the
    # hard stop stands. This assertion used to read the other way round, and
    # its old comment ("the trailing distance is tighter") was stating the
    # bug: a 300p trail silently replaced a 700p stop on the first cycle of
    # every position. See `ExitPlan.trailing_activation`.
    assert _cycle(entry_premium, 1) == []
    assert calls == [], "the trail engaged before the position was in profit"
    with session_factory() as session:
        assert session.query(OpenPositionRow).one().current_stop_paise == stop_before

    # Above activation it DOES ratchet, so the guard is not simply disabling
    # trailing stops. 4_000 is past activation (3_900) and short of the
    # target (3_600 + 1_500 = 5_100).
    in_profit = 4_000
    assert _cycle(in_profit, 2) == []
    assert len(calls) == 1
    with session_factory() as session:
        ratcheted = session.query(OpenPositionRow).one().current_stop_paise
    assert ratcheted > stop_before

    # Every subsequent cycle at the SAME mark makes no new favourable
    # extreme -> nothing to write.
    assert _cycle(in_profit, 3) == []
    assert _cycle(in_profit, 4) == []
    assert len(calls) == 1, "unchanged trailing stop must not be written back"

    with session_factory() as session:
        assert session.query(OpenPositionRow).one().current_stop_paise == ratcheted


def test_no_entry_without_enough_runway_before_the_hard_exit(
    session_factory,  # noqa: ANN001
    execution,  # noqa: ANN001
    cost_model: CostModel,
    tmp_path: Path,
) -> None:
    """A trade needs time to reach its target, or it carries full downside
    against upside that is unreachable by construction.

    Cost real money on 2026-07-31: a NIFTY position opened at 15:05 was
    force-closed at 15:20 for -8.7% (-Rs 6,672, 82% of the day's loss). Its
    stop never fired — the CLOCK closed it.
    """
    store = _breakout_store(tmp_path)
    # 15:05 IST, with a 15:20 hard exit: 15 minutes of runway.
    as_of = dt.datetime(2026, 7, 29, 15, 5, tzinfo=IST)
    config = _config(hard_exit_by=dt.time(15, 20), min_minutes_before_hard_exit=30)

    run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=as_of,
    )

    with session_factory() as session:
        assert session.query(OpenPositionRow).count() == 0
        reasons = [r.reason for r in session.query(SkippedSignalRow).all()]
    assert any("before the hard exit" in r for r in reasons), reasons


def test_entry_is_allowed_with_enough_runway(
    session_factory,  # noqa: ANN001
    execution,  # noqa: ANN001
    cost_model: CostModel,
    tmp_path: Path,
) -> None:
    """The runway rule must not block a normal mid-session entry — the same
    signal, far enough from the close, still trades."""
    store = _breakout_store(tmp_path)
    as_of = _open(61)  # ~10:16 IST, hours of runway
    config = _config(hard_exit_by=dt.time(15, 20), min_minutes_before_hard_exit=30)

    run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=as_of,
    )

    with session_factory() as session:
        assert session.query(OpenPositionRow).count() == 1


def test_disabling_the_percentage_trail_does_not_fall_back_to_the_absolute_one(
    session_factory,  # noqa: ANN001
    execution,  # noqa: ANN001
    cost_model: CostModel,
    tmp_path: Path,
) -> None:
    """`trailing_pct=None` in premium-percentage mode means the trail is OFF.

    It previously fell through to `trailing_distance`, so setting
    `paper_cycle_trailing_pct=None` to disable the trail silently restored
    `paper_cycle_trailing_distance_paise=300` — a Rs 3 absolute trail, which
    on a Rs 81.50 premium is 3.68%, and is the same Rs 3 trail that had
    closed 14 of 14 live trades on `trailing_stop` at a 3.1-minute average
    hold. "Disabled" re-enabled the original bug.
    """
    store = _breakout_store(tmp_path)
    config = _config(
        exit_geometry=PremiumPercentGeometry(stop_pct=Decimal(20), target_pct=Decimal(20), trailing_pct=None)
    )

    run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=_open(61),
    )

    with session_factory() as session:
        row = session.query(OpenPositionRow).one()
    assert row.trailing_distance_paise is None, (
        f"trail is {row.trailing_distance_paise}p despite trailing_pct=None — the absolute fallback fired"
    )


def test_absolute_trailing_distance_still_applies_without_percentage_exits(
    session_factory,  # noqa: ANN001
    execution,  # noqa: ANN001
    cost_model: CostModel,
    tmp_path: Path,
) -> None:
    """The fix must not break configs that legitimately use absolute
    index-point distances (backtests replayed from `option_bhav`, and every
    test predating percentage exits)."""
    store = _breakout_store(tmp_path)
    config = _config(
        exit_geometry=AbsolutePointGeometry(
            stop_distance=Paise(700), target_distance=Paise(1_500), trailing_distance=Paise(300)
        )
    )

    run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=_open(61),
    )

    with session_factory() as session:
        assert session.query(OpenPositionRow).one().trailing_distance_paise == 300
