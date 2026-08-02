"""Tests for te.engine.scheduler.build_scheduler — registers the three
Phase 1 jobs (WS recorder start/stop, bhavcopy ingest, instrument sync) with
the documented IST cron times, the Phase 4/6/7 paper-trading cycle job
(`te.engine.cycle.run_entry_cycle`/`run_exit_cycle`, gated by mode/kill
switch/session window — see `PaperCycleRunner`), and the supervisor's
start/stop lifecycle."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from te.broker.openalgo_login import LoginResult
from te.broker.openalgo_ws import Instrument, OpenAlgoWSClient
from te.data.barstore import BarStore
from te.data.recorder import BarRecorder
from te.domain.calendar import from_holiday_rows
from te.domain.clock import IST
from te.domain.geometry import AbsolutePointGeometry
from te.domain.money import Paise
from te.engine import scheduler as scheduler_module
from te.engine.scheduler import (
    PaperCycleRunner,
    WSRecorderSupervisor,
    build_scheduler,
    run_openalgo_relogin,
    should_start_recorder_now,
)
from te.engine.state import AccountGuardrails, set_guardrails, set_mode, set_run_state
from te.engine.trading_calendar import set_calendar
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base
from te.risk import killswitch
from te.risk.killswitch import trip as trip_killswitch
from te.settings import Settings


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "env": "dev",
        "database_url": "sqlite:///:memory:",
        "bar_store_path": Path("data/bars"),
        "openalgo_host": "http://openalgo:5000",
        "openalgo_ws_host": "ws://openalgo:8765",
        "openalgo_api_key": "test-key",
        "cors_origins": ["http://localhost:5173"],
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_should_start_recorder_now_true_mid_session_weekday() -> None:
    """Regression for 2026-07-30: a mid-day engine redeploy after the 09:10
    IST cron trigger already fired left bar recording silently stopped for
    the rest of the session, since `BackgroundScheduler` has no memory of a
    missed fire. This is the catch-up check `te.api.main`'s lifespan runs
    right after `scheduler.start()`."""
    wednesday_noon_ist = dt.datetime(2026, 7, 29, 12, 0, tzinfo=IST)
    assert should_start_recorder_now(wednesday_noon_ist) is True


def test_should_start_recorder_now_false_outside_window() -> None:
    wednesday_night_ist = dt.datetime(2026, 7, 29, 20, 0, tzinfo=IST)
    assert should_start_recorder_now(wednesday_night_ist) is False


def test_should_start_recorder_now_false_on_weekend() -> None:
    saturday_noon_ist = dt.datetime(2026, 8, 1, 12, 0, tzinfo=IST)
    assert should_start_recorder_now(saturday_noon_ist) is False


def test_should_start_recorder_now_accepts_non_ist_input() -> None:
    """`now` can arrive in any tz (e.g. UTC, as `dt.datetime.now(IST)`'s
    caller might pass through a differently-configured clock) — the check
    must convert, not assume the caller already passed IST."""
    utc_mid_session = dt.datetime(2026, 7, 29, 6, 30, tzinfo=dt.UTC)  # 12:00 IST
    assert should_start_recorder_now(utc_mid_session) is True


def test_fno_lot_size_contracts_are_real_derivative_symbols_not_index_quotes() -> None:
    """Regression for 2026-07-30: instrument sync used to query
    `NSE_INDEX`/`BSE_INDEX` quote symbols (`NIFTY`, `SENSEX`, ...) for lot
    size — an index has no lot size of its own, so the broker honestly
    returned `lotsize=1`/`instrumenttype="INDEX"` for all four, and the
    `instruments` table silently never held a real, usable lot size. Fixed
    by resolving each underlying's real FUT contract instead — futures need
    no strike to exist, and lot size is identical across every FUT/CE/PE
    contract in the same underlying+expiry series.

    Always the MONTHLY expiry, even for NIFTY/SENSEX (whose OPTIONS trade
    weekly) — index FUTURES are monthly-only on NSE/BSE regardless of the
    same underlying's options cadence. An earlier version of this fix used
    the weekly cadence for NIFTY/SENSEX and 404'd against the real broker
    (`NIFTY04AUG26FUT` does not exist; only `NIFTY25AUG26FUT` does)."""
    reference = dt.date(2026, 7, 30)  # a Thursday
    contracts = dict(scheduler_module._fno_lot_size_contracts(reference))

    assert contracts["NIFTY25AUG26FUT"] == "NFO"  # last Tuesday of August
    assert contracts["BANKNIFTY25AUG26FUT"] == "NFO"  # last Tuesday of August
    assert contracts["SENSEX30JUL26FUT"] == "BFO"  # today is the last Thursday of July
    assert contracts["BANKEX30JUL26FUT"] == "BFO"  # today is the last Thursday of July


def test_run_openalgo_relogin_skips_when_not_configured() -> None:
    result = run_openalgo_relogin(_settings())
    assert result == LoginResult(False, "not_configured", "openalgo relogin skipped: credentials not configured")


def test_run_openalgo_relogin_calls_login_openalgo_with_settings_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_login_openalgo(host: str, **kwargs: object) -> LoginResult:
        captured["host"] = host
        captured.update(kwargs)
        return LoginResult(True, "done", "logged in")

    monkeypatch.setattr(scheduler_module, "login_openalgo", fake_login_openalgo)

    settings = _settings(
        openalgo_app_username="app-user",
        openalgo_app_password="app-pass",
        angel_client_id="C123",
        angel_pin="1234",
        angel_totp_secret="JBSWY3DPEHPK3PXP",
    )
    result = run_openalgo_relogin(settings)

    assert result.ok is True
    assert captured["host"] == settings.openalgo_host
    assert captured["app_username"] == "app-user"
    assert captured["app_password"] == "app-pass"
    assert captured["angel_client_id"] == "C123"
    assert captured["angel_pin"] == "1234"
    assert captured["angel_totp_secret"] == "JBSWY3DPEHPK3PXP"


def test_build_scheduler_registers_every_job(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:")
    scheduler, supervisor, runner = build_scheduler(_settings(), engine=engine, bar_store=BarStore(tmp_path))

    job_ids = {job.id for job in scheduler.get_jobs()}
    assert job_ids == {
        "openalgo_relogin",
        "ws_recorder_start",
        "ws_recorder_stop",
        "bhavcopy_ingest",
        "instrument_sync",
        "trading_calendar_refresh",
        "paper_cycle",
    }

    # The one job that must NOT be mon-fri: it is what tells the rest of the
    # engine which weekdays are real sessions, so it runs on a Sunday.
    calendar_job = scheduler.get_job("trading_calendar_refresh")
    assert "sun" in str(calendar_job.trigger.fields[4]).lower()

    ws_start = scheduler.get_job("ws_recorder_start")
    assert "9" in str(ws_start.trigger.fields[5])  # hour field

    relogin_job = scheduler.get_job("openalgo_relogin")
    assert "8" in str(relogin_job.trigger.fields[5])  # hour field — before instrument_sync (08:45)
    ws_stop = scheduler.get_job("ws_recorder_stop")
    assert "15" in str(ws_stop.trigger.fields[5])

    assert isinstance(supervisor, WSRecorderSupervisor)
    assert isinstance(runner, PaperCycleRunner)
    engine.dispose()


def test_paper_cycle_job_registered_with_max_instances_one(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:")
    scheduler, _supervisor, _runner = build_scheduler(_settings(), engine=engine, bar_store=BarStore(tmp_path))

    job = scheduler.get_job("paper_cycle")
    assert job is not None
    assert job.max_instances == 1
    engine.dispose()


def test_ws_client_uses_the_configured_ws_host_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: the WS URL used to be derived by string surgery on
    the REST host (`.replace("http://", "ws://") + ":8765"`), which — since
    `openalgo_host` already carries a port — produced the invalid
    `ws://openalgo:5000:8765`. It must now be the configured value, byte for
    byte, with no derivation."""
    captured: dict[str, str] = {}
    real_cls = scheduler_module.OpenAlgoWSClient

    def _capturing_client(*, url: str, api_key: str) -> object:
        captured["url"] = url
        return real_cls(url=url, api_key=api_key)

    monkeypatch.setattr(scheduler_module, "OpenAlgoWSClient", _capturing_client)

    engine = create_engine("sqlite:///:memory:")
    build_scheduler(
        _settings(openalgo_ws_host="wss://ws.example.internal:9443/stream"),
        engine=engine,
        bar_store=BarStore(tmp_path),
    )

    assert captured["url"] == "wss://ws.example.internal:9443/stream"
    assert captured["url"].count(":") == 2  # scheme colon + one port colon, never two ports
    engine.dispose()


def test_paper_cycle_job_is_weekday_gated_like_every_other_job(tmp_path: Path) -> None:
    """The other four jobs all carry `day_of_week="mon-fri"`. The paper
    cycle used to be a bare `IntervalTrigger`, so it fired every day
    including weekends, relying solely on `PaperCycleRunner.run_once`'s
    time-of-day `is_market_open` check to no-op."""
    engine = create_engine("sqlite:///:memory:")
    scheduler, _supervisor, _runner = build_scheduler(_settings(), engine=engine, bar_store=BarStore(tmp_path))

    job = scheduler.get_job("paper_cycle")
    assert job is not None

    # Next fire time from a Saturday must skip the weekend entirely.
    saturday = dt.datetime(2026, 8, 1, 10, 0, tzinfo=IST)
    assert saturday.weekday() == 5
    next_fire = job.trigger.get_next_fire_time(None, saturday)
    assert next_fire is not None
    assert next_fire.weekday() < 5, f"paper_cycle fires on a weekend: {next_fire}"
    engine.dispose()


def test_paper_cycle_job_not_registered_when_disabled(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:")
    scheduler, _supervisor, _runner = build_scheduler(
        _settings(paper_cycle_enabled=False), engine=engine, bar_store=BarStore(tmp_path)
    )

    assert scheduler.get_job("paper_cycle") is None
    engine.dispose()


def test_ws_recorder_supervisor_start_stop_lifecycle(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    recorder = BarRecorder(store)
    ws_client = OpenAlgoWSClient(url="ws://localhost:1", api_key="test-key")
    ws_client.subscribe([Instrument(exchange="NSE_INDEX", symbol="NIFTY")])

    supervisor = WSRecorderSupervisor(ws_client, recorder)
    assert not supervisor.is_running()

    supervisor.start()
    assert supervisor.is_running()

    supervisor.stop()
    assert not supervisor.is_running()


def test_ws_recorder_supervisor_reports_not_running_when_its_task_has_died(tmp_path: Path) -> None:
    """Regression: thread-liveness alone used to be the whole `is_running()`
    check — `loop.run_forever()` keeps the background thread alive even
    after its one WS task finishes (with or without an exception), so a
    dead task used to still report `is_running() -> True` forever. This is
    exactly the state `GET /api/broker-status` reads to decide
    `connected`. Tests `is_running()`'s logic directly against lightweight
    fakes rather than racing a real thread/event loop."""
    store = BarStore(tmp_path)
    recorder = BarRecorder(store)
    ws_client = OpenAlgoWSClient(url="ws://localhost:1", api_key="test-key")
    supervisor = WSRecorderSupervisor(ws_client, recorder)

    class _FakeThread:
        def is_alive(self) -> bool:
            return True

    class _FakeTask:
        def __init__(self, *, done: bool) -> None:
            self._done = done

        def done(self) -> bool:
            return self._done

    supervisor._thread = _FakeThread()  # type: ignore[assignment]

    supervisor._task = None
    assert supervisor.is_running() is True  # still starting up — not a false negative

    supervisor._task = _FakeTask(done=False)  # type: ignore[assignment]
    assert supervisor.is_running() is True  # thread alive, task still running

    supervisor._task = _FakeTask(done=True)  # type: ignore[assignment]
    assert supervisor.is_running() is False  # thread alive, but its task has died


_CHARGES_PATH = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"


def _cycle_config() -> object:
    from decimal import Decimal

    from te.engine.cycle import CycleConfig
    from te.risk.limits import RiskLimitsConfig

    return CycleConfig(
        mode="paper",
        strategy_name="orb",
        instruments=(),  # empty — no instrument-specific bar data needed; run_entry_cycle/run_exit_cycle are mocked
        exchange="NFO",
        lot_size=65,
        capital=Paise(2_500_000),
        risk_budget_pct=Decimal(2),
        min_edge_multiple=Decimal("1.2"),
        exit_geometry=AbsolutePointGeometry(
            stop_distance=Paise(700), target_distance=Paise(1_500), trailing_distance=Paise(300)
        ),
        max_hold=dt.timedelta(hours=3),
        hard_exit_by=dt.time(15, 20),
        risk_limits=RiskLimitsConfig(
            max_daily_loss_paise=Paise(10_000_00), max_concurrent_positions=5, max_trades_per_day=20
        ),
    )


#: Two real rows from OpenAlgo's 2026 calendar: one ordinary trading
#: holiday, and the Diwali Muhurat special session (epoch-ms bounds are the
#: broker's own, 18:00-19:15 IST).
_HOLIDAY_ROWS: list[dict[str, object]] = [
    {
        "date": "2026-10-02",
        "description": "Mahatma Gandhi Jayanti",
        "holiday_type": "TRADING_HOLIDAY",
        "closed_exchanges": ["NSE", "BSE", "NFO", "BFO"],
        "open_exchanges": [],
    },
    {
        "date": "2026-11-08",
        "description": "Diwali Laxmi Pujan (Muhurat Trading)",
        "holiday_type": "SPECIAL_SESSION",
        "closed_exchanges": [],
        "open_exchanges": [
            {"exchange": "NSE", "start_time": 1794141000000, "end_time": 1794145500000},
            {"exchange": "NFO", "start_time": 1794141000000, "end_time": 1794145500000},
        ],
    },
]


def _runner(tmp_path: Path, *, clock: object) -> PaperCycleRunner:
    from te.data.charges_loader import load_charge_rate_table

    db_engine = make_engine(f"sqlite:///{tmp_path / 'scheduler_cycle_test.db'}")
    Base.metadata.create_all(db_engine)
    session_factory = make_session_factory(db_engine)
    # `get_run_state`'s real default is "paused" (te.engine.state) — every
    # pre-existing test here predates the pause gate and expects the cycle
    # to run unless it explicitly tests halt/mode/session/pause, so set
    # "running" here; `test_paper_cycle_skips_when_paused` overrides it back.
    with session_factory() as session:
        set_run_state(session, "running")
        # The calendar gate (added 2026-08-01) stands the cycle down on any
        # date it cannot classify — including every date, when nothing has
        # been fetched. Seed a real calendar so these tests exercise the
        # gates they are actually about; `test_paper_cycle_skips_on_an
        # _exchange_holiday` covers the calendar gate itself.
        set_calendar(session, from_holiday_rows(_HOLIDAY_ROWS), years=[2026])
        session.commit()
    # `te.risk.killswitch`'s in-process flag (layer 1) is a MODULE-LEVEL
    # global, not per-DB state — a prior test in this file calling
    # `trip_killswitch()` leaves it tripped for every test that runs after
    # it in the same process, regardless of which DB file it uses. Reset it
    # per the module's own documented test-isolation pattern (its docstring:
    # "tests that simulate a restart").
    killswitch.reset_in_process_cache()
    return PaperCycleRunner(
        session_factory=session_factory,
        store=BarStore(tmp_path / "bars"),
        charge_rate_table=load_charge_rate_table(_CHARGES_PATH),
        config=_cycle_config(),  # type: ignore[arg-type]
        max_orders_per_second=5,
        settings=_settings(),
        clock=clock,  # type: ignore[arg-type]
    )


def _during_session_clock() -> dt.datetime:
    return dt.datetime(2026, 7, 29, 10, 0, tzinfo=IST)


def _outside_session_clock() -> dt.datetime:
    return dt.datetime(2026, 7, 29, 20, 0, tzinfo=IST)


def _patch_cycle_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls: dict[str, int] = {"entry": 0, "exit": 0}

    def _entry(**_kw: object) -> int:
        calls["entry"] += 1
        return 0

    def _exit(**_kw: object) -> list[str]:
        calls["exit"] += 1
        return []

    monkeypatch.setattr(scheduler_module, "run_entry_cycle", _entry)
    monkeypatch.setattr(scheduler_module, "run_exit_cycle", _exit)
    return calls


def test_paper_cycle_runs_when_dry_run_and_not_halted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_cycle_calls(monkeypatch)

    runner = _runner(tmp_path, clock=_during_session_clock)
    runner.run_once()

    assert calls == {"entry": 1, "exit": 1}
    assert runner.status.last_result == "ran"
    assert runner.status.last_run_at == _during_session_clock()


def test_paper_cycle_blocks_entries_but_still_runs_exits_when_halted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A halt must never strand an open position with no working exit plan
    — see te/execution/manager.py's submit() docstring for the live bug
    this is a regression test for. New entries ARE blocked."""
    calls = _patch_cycle_calls(monkeypatch)

    runner = _runner(tmp_path, clock=_during_session_clock)
    with runner.session_factory() as session:
        trip_killswitch(session, "test halt")
        session.commit()

    runner.run_once()

    assert calls == {"entry": 0, "exit": 1}
    assert runner.status.last_result == "skipped_halted_entries_only"


def test_paper_cycle_skips_when_mode_is_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_cycle_calls(monkeypatch)

    runner = _runner(tmp_path, clock=_during_session_clock)
    with runner.session_factory() as session:
        set_mode(session, "live")
        session.commit()

    runner.run_once()

    assert calls == {"entry": 0, "exit": 0}
    assert runner.status.last_result == "skipped_mode_live"


def test_paper_cycle_only_runs_during_session_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_cycle_calls(monkeypatch)

    runner = _runner(tmp_path, clock=_outside_session_clock)
    runner.run_once()

    assert calls == {"entry": 0, "exit": 0}
    assert runner.status.last_result == "skipped_outside_session"


def test_paper_cycle_blocks_entries_but_still_manages_exits_when_paused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pause means "stop opening trades", never "stop protecting the ones I
    already have".

    This test previously asserted `exit: 0`, which pinned a real defect: the
    `paused` branch returned above `run_exit_cycle`, so one dashboard click
    disabled every stop-loss, target, trailing stop and the 15:20 hard exit
    on all open positions, leaving them to run unmanaged into the close and
    then overnight. The kill-switch branch immediately below it had already
    been fixed to keep running exits (exiting is risk-reducing); pause was
    the one gate that had not been.
    """
    calls = _patch_cycle_calls(monkeypatch)

    runner = _runner(tmp_path, clock=_during_session_clock)
    with runner.session_factory() as session:
        set_run_state(session, "paused")
        session.commit()

    runner.run_once()

    assert calls == {"entry": 0, "exit": 1}
    assert runner.status.last_result == "skipped_paused_entries_only"


def _patch_cycle_calls_capturing_config(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Like `_patch_cycle_calls`, but records the `config=` kwarg
    `run_entry_cycle` was actually called with each time, so a test can
    assert on ITS content — `_patch_cycle_calls`'s `**_kw` swallows it,
    which can't distinguish "the live config was used" from "some config
    was used"."""
    captured: list[object] = []

    def _entry(**kw: object) -> int:
        captured.append(kw["config"])
        return 0

    def _exit(**_kw: object) -> list[str]:
        return []

    monkeypatch.setattr(scheduler_module, "run_entry_cycle", _entry)
    monkeypatch.setattr(scheduler_module, "run_exit_cycle", _exit)
    return captured


def test_paper_cycle_falls_back_to_env_defaults_with_no_saved_guardrails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback source is `Settings.paper_cycle_*`
    (`guardrails_defaults_from_settings`), NOT `runner.config`'s own
    hardcoded test literals — `_cycle_config()`'s capital is a fixture
    value unrelated to real env-var defaults, so the correct comparison is
    against `runner.settings`, the actual fallback source."""
    from te.engine.state import guardrails_defaults_from_settings

    captured = _patch_cycle_calls_capturing_config(monkeypatch)
    runner = _runner(tmp_path, clock=_during_session_clock)
    expected = guardrails_defaults_from_settings(runner.settings)

    runner.run_once()

    assert len(captured) == 1
    config = captured[0]
    assert config.capital == expected.capital  # type: ignore[attr-defined]
    assert config.risk_limits.max_daily_loss_paise == expected.max_daily_loss  # type: ignore[attr-defined]
    assert config.risk_limits.max_trades_per_day == expected.max_trades_per_day  # type: ignore[attr-defined]


def test_paper_cycle_reads_live_guardrails_without_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core proof for the plan's Tier 1: a guardrails change saved via
    the API takes effect on the VERY NEXT scheduled cycle, on the SAME
    running `PaperCycleRunner` instance — no process restart needed."""
    captured = _patch_cycle_calls_capturing_config(monkeypatch)
    runner = _runner(tmp_path, clock=_during_session_clock)

    runner.run_once()
    first_capital = captured[-1].capital  # type: ignore[attr-defined]

    with runner.session_factory() as session:
        set_guardrails(
            session,
            AccountGuardrails(
                capital=Paise(9_999_900),
                max_daily_loss=Paise(499_995),  # 5% of 9,999,900p — the hard ceiling
                max_position_size_pct=Decimal(25),
                max_drawdown_pct=Decimal(10),
                max_trades_per_day=3,
                max_concurrent_positions=1,
                risk_per_trade_pct=Decimal("1.5"),
            ),
        )
        session.commit()

    runner.run_once()
    second_config = captured[-1]

    assert second_config.capital != first_capital  # type: ignore[attr-defined]
    assert second_config.capital == Paise(9_999_900)  # type: ignore[attr-defined]
    assert second_config.risk_budget_pct == Decimal("1.5")  # type: ignore[attr-defined]
    assert second_config.risk_limits.max_trades_per_day == 3  # type: ignore[attr-defined]
    assert second_config.risk_limits.max_concurrent_positions == 1  # type: ignore[attr-defined]
    assert second_config.risk_limits.max_daily_loss_paise == Paise(499_995)  # type: ignore[attr-defined]


def test_paper_cycle_reads_live_instrument_selections_without_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scheduler-side half of the multi-instrument fix: saved
    `InstrumentSelection`s reach the built `CycleConfig.instrument_configs`
    on the next cycle, each carrying its OWN exchange/lot_size — and an
    inactive selection is excluded entirely, not sized as 0.

    Seeds real `Instrument` sync rows for NIFTY/BANKNIFTY (matching real
    daily operation, where `instrument_sync` runs at 08:45 IST before the
    market opens) — an instrument with NO real sync ever recorded is now
    forced inactive regardless of its saved `active` flag (see
    `te.engine.state._with_real_lot_size`'s "never trade on an unconfirmed
    lot size" rule), so this test would otherwise see zero active
    instruments rather than exercising the real per-instrument config
    path."""
    from te.engine.state import InstrumentSelection, set_instrument_selections
    from te.persistence.models import Instrument as InstrumentRow

    captured = _patch_cycle_calls_capturing_config(monkeypatch)
    runner = _runner(tmp_path, clock=_during_session_clock)

    with runner.session_factory() as session:
        session.add_all(
            [
                InstrumentRow(
                    symbol="NIFTY25AUG26FUT",
                    exchange="NFO",
                    name="NIFTY",
                    instrument_type="FUT",
                    expiry="25-AUG-26",
                    strike=0.0,
                    lot_size=65,
                    tick_size=0.05,
                    source="openalgo",
                    updated_at=dt.datetime.now(dt.UTC),
                ),
                InstrumentRow(
                    symbol="BANKNIFTY25AUG26FUT",
                    exchange="NFO",
                    name="BANKNIFTY",
                    instrument_type="FUT",
                    expiry="25-AUG-26",
                    strike=0.0,
                    lot_size=30,
                    tick_size=0.05,
                    source="openalgo",
                    updated_at=dt.datetime.now(dt.UTC),
                ),
            ]
        )
        set_instrument_selections(
            session,
            (
                InstrumentSelection(symbol="NIFTY", exchange="NFO", lot_size=65),
                InstrumentSelection(symbol="BANKNIFTY", exchange="NFO", lot_size=30),
                InstrumentSelection(symbol="SENSEX", exchange="BFO", lot_size=20, active=False),
            ),
        )
        session.commit()

    runner.run_once()
    config = captured[-1]

    active_symbols = {ic.symbol for ic in config.instrument_configs}  # type: ignore[attr-defined]
    assert active_symbols == {"NIFTY", "BANKNIFTY"}  # SENSEX excluded: active=False

    by_symbol = {ic.symbol: ic for ic in config.instrument_configs}  # type: ignore[attr-defined]
    assert by_symbol["NIFTY"].lot_size == 65
    assert by_symbol["BANKNIFTY"].lot_size == 30
    assert by_symbol["NIFTY"].exchange == "NFO"


def test_a_position_is_quoted_once_per_cycle_not_once_per_consumer() -> None:
    """The entry cycle prices every open position (to decide whether to
    halt) and the exit cycle then prices the same rows (to decide whether to
    close). Uncached, that is two blocking REST round trips per position per
    minute — and worse, the two cycles could act on marks taken a second
    apart, which the caller's own comment claims cannot happen.
    """
    import datetime as dt

    from te.broker.openalgo_rest import Quote
    from te.data.barstore import BarStore
    from te.engine.scheduler import _current_premium_from_quotes

    calls: list[str] = []

    class _Client:
        def quotes(self, symbol: str, exchange: str) -> Quote:
            calls.append(symbol)
            return Quote(
                symbol=symbol, exchange=exchange, ltp=100.0, open=0.0, high=0.0, low=0.0,
                prev_close=0.0, volume=0.0, oi=0.0, bid=99.5, ask=100.5,
            )

    class _Row:
        symbol = "NIFTY04AUG2624400CE"
        exchange = "NFO"
        entry_premium_paise = 10_000
        last_mark_paise = None

    source = _current_premium_from_quotes(
        _Client(),  # type: ignore[arg-type]
        BarStore("unused"),
        dt.datetime(2026, 7, 31, 5, 0, tzinfo=dt.UTC),
    )
    row = _Row()
    first = source(row)  # type: ignore[arg-type]
    second = source(row)  # type: ignore[arg-type]

    assert calls == [_Row.symbol], f"quoted {len(calls)} times for one position in one cycle"
    assert first == second, "the two cycles saw different marks for the same position in the same minute"
