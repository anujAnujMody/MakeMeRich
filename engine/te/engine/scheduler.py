"""APScheduler-based job scheduler — the three Phase 1 jobs from the plan,
plus the Phase 4/6/7 paper-trading cycle job that closes the gap flagged at
the end of those phases (nothing previously called `te.engine.cycle`'s
`run_entry_cycle`/`run_exit_cycle` automatically):

- WS recorder supervisor: starts the live bar recorder at 09:10 IST, stops
  it at 15:35 IST (NSE/BSE cash+F&O session is 09:15-15:30; the 5-minute
  pad on each side covers pre-open auction ticks and a clean final flush).
- bhavcopy ingest: 18:30 IST daily (well after both NSE's and BSE's
  bhavcopy files are typically published for the day).
- instrument sync: 08:45 IST daily, before the WS recorder starts, so lot
  sizes/expiries are current before the session's first bar.
- paper cycle: every `Settings.paper_cycle_interval_minutes` (default 1,
  matching this intraday ORB strategy), during `te.domain.clock`'s session
  window, calls `run_entry_cycle`/`run_exit_cycle` — see
  `PaperCycleRunner.run_once` for the mode/kill-switch/session gates this
  applies before ever touching capital.

Replaces Phase 0's lack of any scheduler — this is new in Phase 1, not a
modification of a Phase 0 file (only `te/api/main.py`'s lifespan wiring
touches Phase 0 code, to start/stop this scheduler with the app).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from te.broker.instrument_sync import fetch_instruments, sync_instruments
from te.broker.openalgo_rest import OpenAlgoRestClient
from te.broker.openalgo_ws import Instrument, OpenAlgoWSClient
from te.broker.ratelimit import TokenBucket
from te.broker.simulated import SimulatedBroker
from te.data.asof import bars_asof
from te.data.barstore import BarStore
from te.data.bhavcopy_bse import ingest_bhavcopy_bse
from te.data.bhavcopy_nse import ingest_bhavcopy_nse
from te.data.charges_loader import load_charge_rate_table
from te.data.recorder import BarRecorder
from te.domain.clock import DEFAULT_SESSION, IST, SessionWindow, is_market_open
from te.domain.costs import ChargeRateTable, CostModel, select_rates
from te.domain.money import Paise
from te.engine.cycle import CycleConfig, run_entry_cycle, run_exit_cycle
from te.engine.state import get_mode
from te.execution.manager import ExecutionManager
from te.execution.store import OrderEventStore
from te.persistence.db import make_session_factory
from te.persistence.models import OpenPositionRow
from te.risk.killswitch import KillSwitchTrippedError
from te.risk.killswitch import check as check_killswitch
from te.risk.limits import RiskLimitsConfig
from te.settings import Settings

logger = logging.getLogger(__name__)

#: Underlyings this project trades. NIFTY/BANKNIFTY quote on NSE, SENSEX on
#: BSE (per the plan's "Capital"/instrument scope). Kept here as the WS
#: subscription list and the instrument-sync contract list — NOT sizing
#: literals (those come from the synced `instruments` table, never this).
NSE_UNDERLYINGS = (("NIFTY", "NSE_INDEX"), ("BANKNIFTY", "NSE_INDEX"))
BSE_UNDERLYINGS = (("SENSEX", "BSE_INDEX"), ("BANKEX", "BSE_INDEX"))


def _now_ist() -> dt.datetime:
    """`PaperCycleRunner.clock`'s default. A module-level function rather
    than a lambda default so it is a valid dataclass field default."""
    return dt.datetime.now(IST)


class WSRecorderSupervisor:
    """Owns the WS client's asyncio event loop on a dedicated background
    thread, so APScheduler's synchronous cron jobs can `start()`/`stop()`
    it without blocking the scheduler's own thread pool."""

    def __init__(self, ws_client: OpenAlgoWSClient, recorder: BarRecorder) -> None:
        self._ws_client = ws_client
        self._recorder = recorder
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task[None] | None = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running():
            logger.warning("WSRecorderSupervisor.start() called while already running — ignoring")
            return

        loop = asyncio.new_event_loop()
        self._loop = loop

        def _on_tick(message: dict[str, object]) -> None:
            self._recorder.on_tick(message)

        def _run_loop() -> None:
            asyncio.set_event_loop(loop)
            self._task = loop.create_task(self._ws_client.run(on_tick=_on_tick))
            try:
                loop.run_forever()
            finally:
                loop.close()

        self._thread = threading.Thread(target=_run_loop, name="ws-recorder-supervisor", daemon=True)
        self._thread.start()
        logger.info("WS recorder supervisor started")

    def stop(self) -> None:
        if not self.is_running() or self._loop is None:
            return

        loop = self._loop
        task = self._task

        async def _shutdown() -> None:
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            self._recorder.flush_all()

        try:
            future = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
            future.result(timeout=10)
        except Exception:
            logger.exception("error while stopping WS recorder supervisor")

        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._thread = None
        self._loop = None
        logger.info("WS recorder supervisor stopped")


def _run_bhavcopy_ingest(engine: Engine, trade_date: dt.date | None = None) -> None:
    target_date = trade_date or dt.datetime.now(IST).date()
    for label, ingest_fn in (("NSE", ingest_bhavcopy_nse), ("BSE", ingest_bhavcopy_bse)):
        try:
            rows = ingest_fn(engine, target_date)
            logger.info("bhavcopy ingest (%s) for %s: %d rows", label, target_date, len(rows))
        except Exception:
            logger.exception("bhavcopy ingest (%s) for %s failed", label, target_date)


def _run_instrument_sync(rest_client: OpenAlgoRestClient, engine: Engine) -> None:
    contracts = list(NSE_UNDERLYINGS) + list(BSE_UNDERLYINGS)
    try:
        rows = fetch_instruments(rest_client, list(contracts))
        written = sync_instruments(engine, rows)
        logger.info("instrument sync: %d rows written", written)
    except Exception:
        logger.exception("instrument sync failed")


def _current_premium_from_bars(store: BarStore, as_of: dt.datetime) -> Callable[[OpenPositionRow], Paise]:
    """The scheduled paper-cycle job's `current_premium` for `run_exit_cycle`
    — the latest CLOSED bar (via `bars_asof`, never a direct `BarStore.read`)
    for the position's symbol. Falls back to the position's own entry
    premium (a neutral, non-crashing mark) if no bar is visible yet at
    `as_of`, e.g. immediately after `run_entry_cycle` opened it in the same
    process before the WS recorder has appended a fresh bar."""

    def _current_premium(row: OpenPositionRow) -> Paise:
        df = bars_asof(store, row.symbol, as_of, lookback=dt.timedelta(minutes=5))
        if df.empty:
            return Paise(row.entry_premium_paise)
        last_close = float(df.iloc[-1]["c"])
        return Paise(int(round(last_close * 100)))

    return _current_premium


def _default_cycle_config(settings: Settings) -> CycleConfig:
    """Builds `CycleConfig` entirely from `Settings.paper_cycle_*` — never a
    hardcoded literal, per the task. An empty `paper_cycle_instruments` is a
    valid (documented) configuration: `run_entry_cycle` simply iterates zero
    instruments, so the job is registered and runs but is a no-op until an
    operator configures instruments."""
    return CycleConfig(
        mode="paper",
        strategy_name=settings.paper_cycle_strategy,
        instruments=settings.paper_cycle_instruments,
        exchange=settings.paper_cycle_exchange,
        lot_size=settings.paper_cycle_lot_size,
        capital=Paise(settings.paper_cycle_capital_paise),
        risk_budget_pct=settings.paper_cycle_risk_budget_pct,
        min_edge_multiple=settings.paper_cycle_min_edge_multiple,
        stop_distance=Paise(settings.paper_cycle_stop_distance_paise),
        target_distance=Paise(settings.paper_cycle_target_distance_paise),
        trailing_distance=(
            Paise(settings.paper_cycle_trailing_distance_paise)
            if settings.paper_cycle_trailing_distance_paise is not None
            else None
        ),
        max_hold=dt.timedelta(minutes=settings.paper_cycle_max_hold_minutes),
        hard_exit_by=settings.paper_cycle_hard_exit_by,
        risk_limits=RiskLimitsConfig(
            max_daily_loss_paise=Paise(settings.paper_cycle_max_daily_loss_paise),
            max_concurrent_positions=settings.paper_cycle_max_concurrent_positions,
            max_trades_per_day=settings.paper_cycle_max_trades_per_day,
        ),
    )


def _paper_cycle_trigger(interval_minutes: int) -> CronTrigger:
    """A WEEKDAY-GATED recurring trigger for the paper-cycle job.

    The other four jobs are all `CronTrigger(..., day_of_week="mon-fri")`;
    this one used to be a bare `IntervalTrigger`, which has no day-of-week
    concept at all — so it woke up every day of the week, weekends included,
    and relied entirely on `PaperCycleRunner.run_once`'s `is_market_open`
    check (which is TIME-OF-DAY only, per `te.domain.clock`) to no-op.

    Expressed as a stepped cron field rather than
    `AndTrigger([IntervalTrigger(...), CronTrigger(...)])`: `AndTrigger`
    fires only when every sub-trigger independently agrees on the SAME
    instant, and a minute-granular interval grid essentially never coincides
    with a daily cron's midnight fire time, so that combination searches
    forward until it overflows rather than ever firing.

    Exchange HOLIDAYS are deliberately still ungated here — that needs
    OpenAlgo's `checkholiday`/`timings` API and is a separate feature."""
    if interval_minutes < 1:
        raise ValueError(f"paper_cycle_interval_minutes must be >= 1, got {interval_minutes!r}")
    if interval_minutes < 60:
        # e.g. 5 -> minute="*/5". Note a non-divisor of 60 (e.g. 7) restarts
        # its count each hour, so the gap across an hour boundary is short —
        # acceptable for a polling job whose default is 1 minute.
        return CronTrigger(day_of_week="mon-fri", minute=f"*/{interval_minutes}", timezone=IST)
    if interval_minutes % 60 != 0:
        raise ValueError(
            f"paper_cycle_interval_minutes must be < 60 or a whole number of hours, got {interval_minutes!r}"
        )
    return CronTrigger(day_of_week="mon-fri", hour=f"*/{interval_minutes // 60}", minute=0, timezone=IST)


@dataclass
class PaperCycleStatus:
    """The observable state `GET /api/engine/scheduler-status` reports —
    when the job last ran (regardless of outcome) and what it did."""

    last_run_at: dt.datetime | None = None
    last_result: str | None = None


@dataclass(kw_only=True)
class PaperCycleRunner:
    """Wraps `run_entry_cycle`/`run_exit_cycle` with the gates an AUTOMATIC
    scheduled job must apply before ever touching capital:

    - **mode gate** — only runs when `te.engine.state.get_mode()` is
      `"dry-run"` (paper). A `live`-mode engine is NEVER auto-run by this
      job: live orders only ever go through the existing per-trade approval
      flow (`te.engine.approvals`, human-triggered), never this cron job.
      This is the safer of the two options the task allows ("must not
      submit live orders without approvals, OR is disabled entirely in live
      mode") — this job disables itself entirely in live mode rather than
      trying to route through approvals from a background job.
    - **kill switch gate** — a halt skips the run entirely: neither
      `run_entry_cycle` nor `run_exit_cycle` is called, so no order (entry
      OR exit) is placed. This is deliberately stricter than
      `run_exit_cycle`'s own contract (which is not itself halt-gated,
      since exiting is risk-REDUCING) — a fully halted engine should not be
      autonomously acting AT ALL; a human runbook (see
      `te.risk.killswitch`'s module docstring, layer 3) is what manages
      existing exposure during a halt, not this scheduled job. A throttle
      (not a halt) does NOT skip the run — `te.engine.cycle.run_entry_cycle`
      already reduces size for a throttled cycle.
    - **session gate** — a no-op outside `te.domain.clock`'s session window
      (never a hardcoded literal).
    """

    session_factory: sessionmaker[Session]
    store: BarStore
    charge_rate_table: ChargeRateTable
    config: CycleConfig
    max_orders_per_second: int
    session_window: SessionWindow = DEFAULT_SESSION
    clock: Callable[[], dt.datetime] = _now_ist
    #: Not an init argument — always starts empty and is rewritten by
    #: `run_once`. `GET /api/engine/scheduler-status` reads it directly.
    status: PaperCycleStatus = field(default_factory=PaperCycleStatus, init=False)

    def run_once(self) -> None:
        as_of = self.clock()
        local = as_of.astimezone(IST)
        if not is_market_open(local, self.session_window):
            self.status = PaperCycleStatus(last_run_at=as_of, last_result="skipped_outside_session")
            return

        with self.session_factory() as session:
            mode = get_mode(session)
        if mode != "dry-run":
            self.status = PaperCycleStatus(last_run_at=as_of, last_result="skipped_mode_live")
            return

        with self.session_factory() as session:
            try:
                check_killswitch(session)
            except KillSwitchTrippedError:
                self.status = PaperCycleStatus(last_run_at=as_of, last_result="skipped_halted")
                return

        cost_model = CostModel(select_rates(self.charge_rate_table, as_of.date()))
        broker = SimulatedBroker(cost_model=cost_model, on=as_of.date())
        rate_limiter = TokenBucket(rate=self.max_orders_per_second, capacity=self.max_orders_per_second)
        execution = ExecutionManager(self.session_factory, OrderEventStore(self.session_factory), broker, rate_limiter)

        run_entry_cycle(
            session_factory=self.session_factory,
            store=self.store,
            execution=execution,
            cost_model=cost_model,
            config=self.config,
            as_of=as_of,
        )
        run_exit_cycle(
            session_factory=self.session_factory,
            execution=execution,
            cost_model=cost_model,
            current_premium=_current_premium_from_bars(self.store, as_of),
            as_of=as_of,
        )
        self.status = PaperCycleStatus(last_run_at=as_of, last_result="ran")


def build_scheduler(
    settings: Settings,
    *,
    engine: Engine,
    bar_store: BarStore | None = None,
) -> tuple[BackgroundScheduler, WSRecorderSupervisor, PaperCycleRunner]:
    """Builds (but does not start) the four-job scheduler (three Phase 1
    jobs plus the Phase 4/6/7 paper-trading cycle), the WS recorder
    supervisor, and the `PaperCycleRunner` the cycle job drives — returned
    so a caller (`GET /api/engine/scheduler-status`) can read
    `runner.status` directly. Caller (typically the FastAPI lifespan) is
    responsible for `.start()`/`.shutdown()`."""
    bar_store = bar_store or BarStore(settings.bar_store_path)
    rest_client = OpenAlgoRestClient(host=settings.openalgo_host, api_key=settings.openalgo_api_key.get_secret_value())
    # Used verbatim — see `Settings.openalgo_ws_host` for why this is a
    # separate, explicitly-configured endpoint and never derived from
    # `openalgo_host`.
    ws_client = OpenAlgoWSClient(
        url=settings.openalgo_ws_host, api_key=settings.openalgo_api_key.get_secret_value()
    )
    ws_client.subscribe(
        [Instrument(exchange=exch, symbol=symbol) for symbol, exch in (*NSE_UNDERLYINGS, *BSE_UNDERLYINGS)]
    )
    recorder = BarRecorder(bar_store)
    supervisor = WSRecorderSupervisor(ws_client, recorder)

    session_factory = make_session_factory(engine)
    charge_rate_table = load_charge_rate_table(settings.charges_path)
    paper_cycle_runner = PaperCycleRunner(
        session_factory=session_factory,
        store=bar_store,
        charge_rate_table=charge_rate_table,
        config=_default_cycle_config(settings),
        max_orders_per_second=settings.max_orders_per_second,
    )

    scheduler = BackgroundScheduler(timezone=IST)
    scheduler.add_job(
        supervisor.start,
        trigger=CronTrigger(hour=9, minute=10, day_of_week="mon-fri", timezone=IST),
        id="ws_recorder_start",
        replace_existing=True,
    )
    scheduler.add_job(
        supervisor.stop,
        trigger=CronTrigger(hour=15, minute=35, day_of_week="mon-fri", timezone=IST),
        id="ws_recorder_stop",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_bhavcopy_ingest,
        args=[engine],
        trigger=CronTrigger(hour=18, minute=30, day_of_week="mon-fri", timezone=IST),
        id="bhavcopy_ingest",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_instrument_sync,
        args=[rest_client, engine],
        trigger=CronTrigger(hour=8, minute=45, day_of_week="mon-fri", timezone=IST),
        id="instrument_sync",
        replace_existing=True,
    )
    if settings.paper_cycle_enabled:
        scheduler.add_job(
            paper_cycle_runner.run_once,
            trigger=_paper_cycle_trigger(settings.paper_cycle_interval_minutes),
            id="paper_cycle",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )

    return scheduler, supervisor, paper_cycle_runner
