"""Tests for te.engine.scheduler.build_scheduler — registers the three
Phase 1 jobs (WS recorder start/stop, bhavcopy ingest, instrument sync) with
the documented IST cron times, the Phase 4/6/7 paper-trading cycle job
(`te.engine.cycle.run_entry_cycle`/`run_exit_cycle`, gated by mode/kill
switch/session window — see `PaperCycleRunner`), and the supervisor's
start/stop lifecycle."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from te.broker.openalgo_ws import Instrument, OpenAlgoWSClient
from te.data.barstore import BarStore
from te.data.recorder import BarRecorder
from te.domain.clock import IST
from te.domain.money import Paise
from te.engine import scheduler as scheduler_module
from te.engine.scheduler import PaperCycleRunner, WSRecorderSupervisor, build_scheduler
from te.engine.state import set_mode
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base
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


def test_build_scheduler_registers_all_four_jobs(tmp_path: Path) -> None:
    engine = create_engine("sqlite:///:memory:")
    scheduler, supervisor, runner = build_scheduler(_settings(), engine=engine, bar_store=BarStore(tmp_path))

    job_ids = {job.id for job in scheduler.get_jobs()}
    assert job_ids == {"ws_recorder_start", "ws_recorder_stop", "bhavcopy_ingest", "instrument_sync", "paper_cycle"}

    ws_start = scheduler.get_job("ws_recorder_start")
    assert "9" in str(ws_start.trigger.fields[5])  # hour field
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
        stop_distance=Paise(700),
        target_distance=Paise(1_500),
        trailing_distance=Paise(300),
        max_hold=dt.timedelta(hours=3),
        hard_exit_by=dt.time(15, 20),
        risk_limits=RiskLimitsConfig(
            max_daily_loss_paise=Paise(10_000_00), max_concurrent_positions=5, max_trades_per_day=20
        ),
    )


def _runner(tmp_path: Path, *, clock: object) -> PaperCycleRunner:
    from te.data.charges_loader import load_charge_rate_table

    db_engine = make_engine(f"sqlite:///{tmp_path / 'scheduler_cycle_test.db'}")
    Base.metadata.create_all(db_engine)
    session_factory = make_session_factory(db_engine)
    return PaperCycleRunner(
        session_factory=session_factory,
        store=BarStore(tmp_path / "bars"),
        charge_rate_table=load_charge_rate_table(_CHARGES_PATH),
        config=_cycle_config(),  # type: ignore[arg-type]
        max_orders_per_second=5,
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


def test_paper_cycle_skips_when_halted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_cycle_calls(monkeypatch)

    runner = _runner(tmp_path, clock=_during_session_clock)
    with runner.session_factory() as session:
        trip_killswitch(session, "test halt")
        session.commit()

    runner.run_once()

    assert calls == {"entry": 0, "exit": 0}
    assert runner.status.last_result == "skipped_halted"


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
