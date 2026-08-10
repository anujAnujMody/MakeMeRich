"""APScheduler-based job scheduler — the three Phase 1 jobs from the plan,
plus the Phase 4/6/7 paper-trading cycle job that closes the gap flagged at
the end of those phases (nothing previously called `te.engine.cycle`'s
`run_entry_cycle`/`run_exit_cycle` automatically):

- WS recorder supervisor: starts the live bar recorder at 09:10 IST, stops
  it at 15:35 IST (NSE/BSE cash+F&O session is 09:15-15:30; the 5-minute
  pad on each side covers pre-open auction ticks and a clean final flush).
- WS feed health check: every 5 minutes 09:00-15:55 IST, verifies ticks are
  actually arriving (not just that the WS thread is alive) and self-heals
  by bouncing the recorder if the feed has gone stale — see
  `FeedHealthStatus`.
- bhavcopy ingest: hourly 19:00-23:00 IST, skipping whichever exchange has
  already landed. It used to be a single 18:30 attempt, on the belief that
  this was "well after" both files were published; it is not. NSE publishes
  its F&O bhavcopy around 20:00, so on 2026-08-03 the one attempt 404'd and
  that day's NIFTY/BANKNIFTY premiums went missing until re-fetched by
  hand. Retrying costs nothing once the file is in — see
  `te.data.ingest_log.already_ingested`.
- instrument sync: 08:45 IST daily, before the WS recorder starts, so lot
  sizes/expiries are current before the session's first bar.
- OpenAlgo relogin: 08:40 IST daily, before instrument sync — Angel expires
  its broker session nightly (a real Angel-platform behaviour, not an
  OpenAlgo bug); see `te.broker.openalgo_login`. It IS retried on process
  restart, the way the WS recorder catch-up is — see `relogin_catchup_due`.
  This paragraph used to say the opposite, on the grounds that a login
  replays real credentials and a dev `--reload` loop would hit Angel's rate
  limiter. That objection was right about the mechanism and wrong about the
  conclusion: on 2026-08-05 the 08:40 cron did not fire, every quote
  returned HTTP 500 "Failed to fetch LTP" through the pre-open, and recovery
  needed a hand-run `POST /api/engine/relogin-broker` at 08:55. Recording
  the date of the last SUCCESSFUL login removes the objection instead of
  accepting it — the second and every later restart on the same day is a
  no-op. That endpoint still covers the ad-hoc case.
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
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import partial

import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from te.broker.instrument_sync import fetch_instruments, sync_instruments
from te.broker.openalgo_login import LoginResult, login_openalgo
from te.broker.openalgo_rest import OpenAlgoRestClient, OpenAlgoRestError
from te.broker.openalgo_ws import Instrument, OpenAlgoWSClient
from te.data.asof import bars_asof
from te.data.barstore import BarStore
from te.data.bhavcopy_bse import SOURCE as BSE_BHAV_SOURCE
from te.data.bhavcopy_bse import ingest_bhavcopy_bse
from te.data.bhavcopy_nse import SOURCE as NSE_BHAV_SOURCE
from te.data.bhavcopy_nse import ingest_bhavcopy_nse
from te.data.charges_loader import load_charge_rate_table
from te.data.ingest_log import already_ingested
from te.audit.session_audit import AuditLimits, SessionAudit, audit_session
from te.data.recorder import BarRecorder
from te.domain.calendar import TradingCalendar
from te.domain.clock import IST, SessionWindow, is_market_open
from te.domain.costs import ChargeRateTable, CostModel
from te.domain.geometry import (
    AbsolutePointGeometry,
    ExitGeometry,
    PremiumPercentGeometry,
    RupeeRiskGeometry,
)
from te.domain.money import Paise
from te.domain.symbols import FNO_UNDERLYING_EXCHANGES, build_future_symbol, next_monthly_expiry
from te.engine.contract import UNDERLYING_INDEX_EXCHANGES, ContractResolver, OptionContractResolver
from te.engine.cycle import CycleConfig, InstrumentConfig, run_entry_cycle, run_exit_cycle
from te.engine.state import (
    clamp_guardrails,
    get_capital_set_at,
    get_guardrails,
    get_instrument_selections,
    get_last_broker_relogin_day,
    get_mode,
    get_run_state,
    guardrails_defaults_from_settings,
    instrument_selections_defaults_from_settings,
    set_last_broker_relogin_day,
)
from te.engine.trading_calendar import get_calendar, refresh_calendar
from te.execution.halt import clear_daily_halt, halt_reason
from te.execution.manager import build_paper_execution_stack
from te.ml.gates import MLHook
from te.ml.nightly import ShadowHookProvider, run_nightly_training
from te.persistence.db import make_session_factory, session_scope
from te.persistence.models import OpenPositionRow
from te.persistence.repos.paper_trading import record_risk_event
from te.risk.killswitch import KillSwitchTrippedError
from te.risk.killswitch import check as check_killswitch
from te.risk.limits import RiskLimitsConfig
from te.settings import Settings

logger = structlog.get_logger(__name__)

#: Underlyings this project trades. NIFTY/BANKNIFTY quote on NSE, SENSEX/
#: BANKEX on BSE (per the plan's "Capital"/instrument scope). This is the WS
#: subscription list — quote-only `_INDEX` symbols, correct for bar
#: recording. NOT the instrument-sync contract list (see
#: `_fno_lot_size_contracts` below): an index has no lot size, so resolving
#: real lot sizes needs the underlying's actual F&O contract, not its spot
#: quote symbol.
NSE_UNDERLYINGS = (("NIFTY", "NSE_INDEX"), ("BANKNIFTY", "NSE_INDEX"))
BSE_UNDERLYINGS = (("SENSEX", "BSE_INDEX"), ("BANKEX", "BSE_INDEX"))


def _option_instruments(resolver: OptionContractResolver, settings: Settings) -> list[Instrument]:
    """The option contracts to RECORD, resolved fresh at recorder start.

    Every backtest in this engine scores strategies on option premiums, but
    until 2026-08-03 the recorder subscribed to the four index symbols and
    nothing else — so no premium was ever captured live, and the only source
    was the exchange's once-a-day bhavcopy. A day's trading could not be
    measured until the following morning.

    Recording a strike band per underlying (see
    `OptionContractResolver.strike_band`) closes that: the premiums the
    strategies are scored on are archived minute by minute, from the same
    feed the live cycle trades against.

    `band=0` switches option recording off entirely and restores the
    index-only behaviour.
    """
    band = settings.recorder_strike_band
    if band <= 0:
        logger.info("option recording disabled (recorder_strike_band=0)")
        return []
    as_of = dt.datetime.now(IST)
    instruments: list[Instrument] = []
    # Driven by `UNDERLYING_INDEX_EXCHANGES` rather than the
    # `NSE_UNDERLYINGS`/`BSE_UNDERLYINGS` pairs above: that map is what
    # `strike_band` itself consults, so an underlying added to one and not
    # the other would resolve to zero strikes and record nothing, silently.
    for underlying in UNDERLYING_INDEX_EXCHANGES:
        for symbol, exchange in resolver.strike_band(underlying, as_of, band=band):
            instruments.append(Instrument(exchange=exchange, symbol=symbol))
    return instruments


def _fno_lot_size_contracts(reference: dt.date) -> list[tuple[str, str]]:
    """The real, always-listed FUTURES contract per underlying, as of
    `reference` — the instrument-sync contract list. Querying an index's
    `_INDEX` quote symbol (as this used to) returns `lotsize=1` and
    `instrumenttype="INDEX"` from the broker, since an index itself has no
    lot size; only its derivative contracts do. A future is used rather than
    an option because it needs no strike to exist — lot size is identical
    across every FUT/CE/PE contract in the same underlying+expiry series, so
    the future is the simplest contract guaranteed to be listed.

    Always the MONTHLY expiry: index FUTURES are monthly-only on NSE/BSE
    even when the same underlying's OPTIONS trade weekly (confirmed live
    against the broker — a weekly-cadence future symbol 404s)."""
    return [
        (build_future_symbol(base, next_monthly_expiry(base, reference)), exchange)
        for base, exchange in FNO_UNDERLYING_EXCHANGES
    ]


def _now_ist() -> dt.datetime:
    """`PaperCycleRunner.clock`'s default. A module-level function rather
    than a lambda default so it is a valid dataclass field default."""
    return dt.datetime.now(IST)


@dataclass(frozen=True)
class LateSubscriptionStatus:
    """Outcome of the last attempt to resolve and subscribe the run-time
    instruments (today's option strikes).

    `subscribed == 0` with `error is None` means it has never been tried —
    distinct from tried-and-failed, which carries the reason.
    """

    attempted_at: dt.datetime | None = None
    subscribed: int = 0
    error: str | None = None


@dataclass(frozen=True)
class FeedHealthStatus:
    """Outcome of the last `check_feed_health` run.

    Exists because "the WS thread is alive" and "ticks are actually
    arriving" are different facts. Found live on 2026-08-04: an ad-hoc
    broker relogin made OpenAlgo delete its Angel WS adapter, but this
    engine's own WS connection to OpenAlgo never dropped — so `is_running()`
    stayed `True`, no reconnect ever fired, and every tick, index and
    option, silently stopped for about an hour before a human noticed by
    hand. `healed=True` means this check found the feed stale and
    bounced the recorder itself, rather than just logging and waiting.
    """

    checked_at: dt.datetime | None = None
    last_tick_at: dt.datetime | None = None
    healed: bool = False


class WSRecorderSupervisor:
    """Owns the WS client's asyncio event loop on a dedicated background
    thread, so APScheduler's synchronous cron jobs can `start()`/`stop()`
    it without blocking the scheduler's own thread pool."""

    def __init__(
        self,
        ws_client: OpenAlgoWSClient,
        recorder: BarRecorder,
        *,
        late_instruments: Callable[[], list[Instrument]] | None = None,
    ) -> None:
        self._ws_client = ws_client
        self._recorder = recorder
        #: Resolved on every refresh rather than once at build time — see
        #: `refresh_late_instruments`.
        self._late_instruments = late_instruments
        self._late_status = LateSubscriptionStatus()
        self._feed_health = FeedHealthStatus()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task[None] | None = None
        #: Set on every `start()` (including a self-heal restart) — gives
        #: `check_feed_health` a grace period before the first tick has had
        #: a realistic chance to arrive, instead of flagging a feed that
        #: hasn't been up long enough to prove itself either way.
        self._started_at: dt.datetime | None = None

    @property
    def late_status(self) -> LateSubscriptionStatus:
        """The outcome of the last `refresh_late_instruments` attempt.

        Exists because the failure this guards against is otherwise
        invisible: `is_running()` reports the FEED, which is perfectly
        healthy on a day where every option strike failed to resolve and not
        one premium was recorded. Observed live on 2026-08-03 — the engine
        restarted before the OpenAlgo gateway was reachable, every broker
        call raised, and nothing anywhere said so.
        """
        return self._late_status

    @property
    def feed_health(self) -> FeedHealthStatus:
        """The outcome of the last `check_feed_health` run — see
        `FeedHealthStatus` for the failure mode this exists to catch."""
        return self._feed_health

    def check_feed_health(self, *, stale_after: dt.timedelta = dt.timedelta(minutes=3)) -> None:
        """Verifies ticks are actually arriving, not just that the WS
        thread is alive — see `FeedHealthStatus`'s docstring for the exact
        2026-08-04 incident this closes. Self-heals (stop then start) the
        moment it finds the feed stale, rather than only logging: a bounce
        is exactly the fix a human would apply by hand, and there is no
        reason to wait for one when the recorder can apply it itself within
        one 5-minute check cycle instead of however long it takes someone to
        notice.

        A no-op when the recorder isn't running at all — matches
        `refresh_late_instruments`'s convention of staying quiet outside the
        session and on holidays, when `start()` never fired."""
        if not self.is_running():
            return
        now = dt.datetime.now(dt.UTC)
        started_at = self._started_at
        if started_at is not None and now - started_at < stale_after:
            # Just (re)started — the first tick can take a few seconds, and
            # a self-heal resets this clock. Flagging a feed that hasn't had
            # time to prove itself either way would just bounce it forever.
            return
        last_tick = self._recorder.last_tick_at
        stale = last_tick is None or (now - last_tick) > stale_after
        self._feed_health = FeedHealthStatus(checked_at=now, last_tick_at=last_tick, healed=stale)
        if not stale:
            return
        logger.error(
            "feed is stale — no ticks recently, reconnecting",
            last_tick_at=last_tick.isoformat() if last_tick else None,
            stale_after_seconds=stale_after.total_seconds(),
        )
        self.stop()
        self.start()

    def refresh_late_instruments(self) -> None:
        """Re-resolves the subscriptions that can only be known at run time
        and hands the CURRENT desired set to the WS client.

        Option strikes are the reason this exists, and also the reason it is
        not inline in `start()`. Which contract is at-the-money depends on
        where the index is trading and which expiry is nearest, so resolving
        in `build_scheduler` (once, at process start — possibly overnight,
        possibly days earlier) would subscribe to yesterday's strikes on a
        stale expiry. But a band costs one broker round trip per strike —
        ~88 of them, measured at ~32s — so `start()` runs this on a
        throwaway thread and the feed comes up on the indices immediately.

        Called REPEATEDLY, not once: on every `start()`, and on a timer
        while the recorder runs. One-shot resolution turned a transient
        broker outage into a whole day with no premium data (2026-08-03,
        engine up before the gateway), and cannot follow the at-the-money
        strike as the index drifts or the expiry rolls. Re-asking is the
        retry, the drift correction and the rollover, all in one — which is
        why `set_dynamic_subscriptions` replaces rather than accumulates.

        Every failure is swallowed but never silent: this is additive
        coverage, and neither a broker hiccup nor a slow chain may cost us
        the index recording — but each outcome lands in `late_status`.
        """
        resolve = self._late_instruments
        loop = self._loop
        if resolve is None:
            return
        attempted_at = dt.datetime.now(IST)
        if loop is None or not self.is_running():
            self._late_status = LateSubscriptionStatus(attempted_at, error="recorder is not running")
            return
        try:
            instruments = resolve()
        except Exception as exc:
            logger.exception("could not resolve late instruments — recording indices only")
            self._late_status = LateSubscriptionStatus(attempted_at, error=f"resolve failed: {exc}")
            return
        if not instruments:
            # Previously this returned silently, which is exactly how a full
            # day of missing premiums went unnoticed.
            logger.warning("late instrument resolution produced nothing — recording indices only")
            self._late_status = LateSubscriptionStatus(attempted_at, error="resolved no instruments")
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._ws_client.set_dynamic_subscriptions(instruments), loop)
            # Waiting is what surfaces an exception out of the coroutine;
            # the resolve above already cost ~32s, this send is milliseconds.
            future.result(timeout=60)
        except Exception as exc:
            logger.exception("could not subscribe late instruments — recording indices only")
            self._late_status = LateSubscriptionStatus(attempted_at, error=f"subscribe failed: {exc}")
            return
        self._late_status = LateSubscriptionStatus(attempted_at, subscribed=len(instruments))
        logger.info("subscribed late instruments", count=len(instruments))

    def is_running(self) -> bool:
        """`True` only when the background thread is alive AND its WS task
        hasn't finished. Thread-liveness alone used to be the whole check —
        but `loop.run_forever()` keeps the thread alive even after its one
        task dies (the exact "task dies, thread lives on, recording
        silently stops forever" failure mode `openalgo_ws.py`'s per-tick
        exception handling now mostly prevents, but doesn't eliminate for
        every possible failure). `self._task is None` means `start()` is
        still mid-launch (the task is assigned on the background thread,
        racy to observe from the caller's thread right after `start()`
        returns) — treated as running rather than a false negative."""
        if self._thread is None or not self._thread.is_alive():
            return False
        return self._task is None or not self._task.done()

    def start(self) -> None:
        if self.is_running():
            logger.warning("WSRecorderSupervisor.start() called while already running — ignoring")
            return

        loop = asyncio.new_event_loop()
        self._loop = loop
        self._started_at = dt.datetime.now(dt.UTC)

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
        # On its own thread, and only after the loop thread exists: this
        # blocks for ~32s of broker calls, and `start()` is on the FastAPI
        # startup path. The periodic refresh job would pick it up anyway,
        # but not for several minutes — too long to leave a restart with no
        # premium recording.
        threading.Thread(target=self.refresh_late_instruments, name="ws-late-subscribe", daemon=True).start()
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


#: Same 5-minute pad as the `ws_recorder_start`/`ws_recorder_stop` cron
#: triggers below (09:10-15:35 IST) — kept as a separate window rather than
#: `DEFAULT_SESSION` since the recorder intentionally runs slightly wider
#: than the 09:15-15:30 trading session itself.
_RECORDER_WINDOW = SessionWindow(start=dt.time(9, 10), end=dt.time(15, 35))


def should_start_recorder_now(now: dt.datetime, calendar: TradingCalendar | None = None) -> bool:
    """True when `now` falls inside today's WS-recording window on a trading
    weekday — the catch-up check the FastAPI lifespan runs right after
    `scheduler.start()`, so a mid-session engine restart doesn't silently
    lose the rest of the day's bars. The 09:10 cron trigger fires exactly
    once; if it already fired earlier today and the process then restarted
    (a redeploy, a crash), `BackgroundScheduler` has no memory of that and
    will not fire it again until tomorrow — found live on 2026-07-30, when a
    same-day engine redeploy silently stopped bar recording for the rest of
    the session with no error anywhere."""
    ist_now = now.astimezone(IST)
    if calendar is not None:
        # `calendar` is optional so the pure weekday form stays available to
        # tests and to any caller with no DB — but the live lifespan passes
        # one, because a weekday check alone restarts the recorder on every
        # exchange holiday to subscribe to a feed that never ticks.
        if not calendar.is_trading_day(ist_now.date(), exchange="NSE"):
            return False
    elif ist_now.weekday() >= 5:  # Saturday/Sunday — no session, no recording.
        return False
    return is_market_open(ist_now, _RECORDER_WINDOW)


def _run_calendar_refresh(session_factory: sessionmaker[Session], rest_client: OpenAlgoRestClient) -> None:
    """Refreshes the stored exchange holiday calendar. Also pulls NEXT year
    once December starts, so a running engine crossing 1 January does not
    spend the first week of the year unable to classify a date — the failure
    mode there is a full stand-down (`TradingCalendar.unknown()`), which is
    safe but useless."""
    now = dt.datetime.now(IST)
    refresh_calendar(session_factory, rest_client, year=now.year)
    if now.month == 12:
        refresh_calendar(session_factory, rest_client, year=now.year + 1)


def _run_bhavcopy_ingest(engine: Engine, trade_date: dt.date | None = None) -> None:
    """Both exchanges' bhavcopy for `trade_date`, skipping whichever already
    landed.

    Safe to run repeatedly, which is the point: this is scheduled hourly
    through the evening rather than once, because the exchanges publish when
    they publish. On 2026-08-03 the single 18:30 attempt got a 404 from NSE
    for a file that downloaded fine at 19:35 — NSE's F&O bhavcopy appears
    around 20:00 IST — so that day's NIFTY and BANKNIFTY option premiums
    were missing, and would have stayed missing, with only a log line to say
    so. BSE published on time the same evening, hence the per-source skip:
    the exchange that already succeeded must not be re-fetched four more
    times while waiting for the one that hasn't.
    """
    target_date = trade_date or dt.datetime.now(IST).date()
    for label, source, ingest_fn in (
        ("NSE", NSE_BHAV_SOURCE, ingest_bhavcopy_nse),
        ("BSE", BSE_BHAV_SOURCE, ingest_bhavcopy_bse),
    ):
        if already_ingested(engine, source=source, trade_date=target_date):
            logger.debug("bhavcopy already ingested — skipping", exchange=label, trade_date=str(target_date))
            continue
        try:
            rows = ingest_fn(engine, target_date)
            logger.info("bhavcopy ingest complete", exchange=label, trade_date=str(target_date), rows=len(rows))
        except Exception:
            # Not fatal, and not final: the next hourly attempt retries it.
            logger.warning("bhavcopy ingest failed — will retry", exchange=label, trade_date=str(target_date))


def _run_instrument_sync(rest_client: OpenAlgoRestClient, engine: Engine) -> None:
    """Real F&O contracts only (see `_fno_lot_size_contracts`) — NOT
    `NSE_UNDERLYINGS`/`BSE_UNDERLYINGS`, which are `_INDEX` quote symbols
    with no lot size of their own. Each underlying's failure is isolated:
    one bad/delisted contract must not blank out the other three's already-
    correct rows."""
    reference = dt.datetime.now(IST).date()
    written_total = 0
    for symbol, exchange in _fno_lot_size_contracts(reference):
        try:
            rows = fetch_instruments(rest_client, [(symbol, exchange)])
            written_total += sync_instruments(engine, rows)
        except Exception:
            logger.exception("instrument sync failed for one contract", symbol=symbol, exchange=exchange)
    logger.info("instrument sync complete", rows_written=written_total)


def run_session_audit(session_factory: sessionmaker[Session], settings: Settings) -> SessionAudit | None:
    """Audits the session that just ended and logs the result. Returns the
    audit (or `None` if it could not run at all).

    Scheduled for 15:35 IST — after the 15:30 close and after the 15:15 hard
    exit, so every position the day opened has resolved and the numbers are
    final.

    Why a scheduled job and not just a test: on 2026-08-07 the engine broke
    FOUR of its own limits in a single session while the suite was green,
    because the tests and the code shared one wrong idea about what "a trade
    today" means. A test can only check the failures somebody thought to
    imagine. This checks the day that actually happened, so a rule breaking in
    a way nobody predicted still gets caught — the same evening, not weeks
    later.

    Logs at ERROR on a violation. That is deliberate: a violated risk limit is
    not a warning, it is the engine having done something it promised it would
    not do.
    """
    now = dt.datetime.now(IST)
    try:
        with session_factory() as session:
            guardrails = get_guardrails(session, defaults=guardrails_defaults_from_settings(settings))
            limits = AuditLimits(
                max_trades_per_day=guardrails.max_trades_per_day,
                max_concurrent_positions=guardrails.max_concurrent_positions,
                max_daily_loss_paise=int(guardrails.max_daily_loss),
                max_consecutive_losses=settings.paper_cycle_max_consecutive_losses,
                max_entries_per_underlying_per_day=settings.paper_cycle_max_entries_per_underlying_per_day,
                max_loss_per_trade_paise=settings.paper_cycle_max_loss_per_trade_paise,
                max_lots=settings.paper_cycle_max_lots,
            )
            audit = audit_session(session, on=now.date(), limits=limits, now=now)
    except Exception:
        # Never let the audit take the scheduler down with it. An audit that
        # crashes is a lost report; an audit that kills the job thread is a
        # silently missing report every day after.
        logger.exception("session audit failed to run")
        return None

    if not audit.entries:
        logger.info("session audit: no entries today", on=audit.on.isoformat())
        return audit

    if audit.violations:
        logger.error(
            "SESSION AUDIT FAILED — the engine broke its own limits",
            on=audit.on.isoformat(),
            entries=len(audit.entries),
            net_rupees=audit.net_paise / 100,
            violations=[f.rule for f in audit.violations],
        )
        for finding in audit.violations:
            logger.error(
                "audit violation",
                rule=finding.rule,
                expected=finding.expected,
                actual=finding.actual,
                detail=finding.detail,
            )
    else:
        logger.info(
            "session audit clean",
            on=audit.on.isoformat(),
            entries=len(audit.entries),
            net_rupees=audit.net_paise / 100,
        )
    for finding in audit.warnings:
        logger.warning("audit warning", rule=finding.rule, actual=finding.actual, detail=finding.detail)
    return audit


def reset_daily_loss_halt(session_factory: sessionmaker[Session]) -> bool:
    """Clears yesterday's daily-loss halt so today can trade. Returns whether
    anything was cleared.

    The daily loss limit is scoped to one day by definition, but nothing ever
    unscoped it: `set_halt` persists, and the only caller of `clear_halt` was
    `POST /api/engine/reset-drawdown-breaker`. So the first session to breach
    the limit blocked every entry from then on, until a human noticed and
    clicked a button. On an unattended 1-2 month paper run that quietly costs
    weeks of data, and — worse — it looks exactly like a strategy that simply
    found no signals, so nothing about the dashboard would say anything was
    wrong.

    Narrow on purpose: `clear_daily_halt` refuses to clear a drawdown breach,
    an overfill, a reconciliation mismatch, or the kill switch. None of those
    is a per-day condition and every one of them means a human has to look at
    something. The reset is also RECORDED as a risk event rather than done
    silently — an engine that un-halts itself overnight with no trace is not
    something anyone should have to discover by reading logs."""
    with session_scope(session_factory) as session:
        previous = halt_reason(session)
        if not clear_daily_halt(session):
            return False
        record_risk_event(
            session,
            ts=dt.datetime.now(dt.UTC),
            kind="daily_loss_halt_reset",
            detail=f"new session: cleared yesterday's daily-loss halt ({previous})",
        )
    logger.info("daily loss halt reset for the new session", previous_reason=previous)
    return True


def run_nightly_training_job(
    session_factory: sessionmaker[Session],
    engine: Engine,
    store: BarStore,
    charge_rate_table: ChargeRateTable,
    settings: Settings,
) -> None:
    """Trains and REGISTERS the shadow model on the day's accumulated
    evaluations. Never promotes it — see `te.ml.nightly`.

    The instrument list is read from `te.engine.state` at run time rather
    than captured at build time, so it follows the same dashboard selection
    the trading cycle uses. A model trained on instruments the engine no
    longer trades would be learning about a different system than the one
    running.

    Cannot affect trading: the model it writes is consumed only at `shadow`
    stage, where `MaturityGate.influence` is a structural no-op.
    """
    with session_factory() as session:
        selections = get_instrument_selections(
            session, defaults=instrument_selections_defaults_from_settings(settings)
        )
    instruments = tuple(s.symbol for s in selections if s.active)
    # The WHOLE table, not one day's row: labelling walks years of firings
    # and each must be priced at the rates in force on its own date.
    run_nightly_training(
        session_factory,
        engine,
        store,
        CostModel(charge_rate_table),
        # The SAME geometry the entry cycle trades, not a second copy of the
        # numbers. Labels used to be computed at a hardcoded 20% stop and 20%
        # target — 1:1 — carrying a comment saying they "must track" two
        # settings the live config had already stopped using. By the time it
        # was found the engine was trading a Rs 700 rupee cap at a 10x target
        # multiple (~1:10), so every label described a strategy that was not
        # running, and anything trained or filtered from them was tuned for a
        # machine that did not exist.
        #
        # `_exit_geometry` is the one true selector, so passing its result
        # here is what makes the two halves incapable of drifting apart
        # again. `run_nightly_training` takes it as a REQUIRED argument for
        # the same reason: a default would just be the old bug with a longer
        # fuse.
        geometry=_exit_geometry(settings),
        max_lots=settings.paper_cycle_max_lots or 1,
        instruments=instruments,
    )


#: The relogin is due from its 08:40 cron time, and stays due until the
#: session ends — a login at 11:00 still saves the rest of the day, and the
#: alternative is every quote failing until someone notices by hand.
_RELOGIN_WINDOW = SessionWindow(start=dt.time(8, 40), end=dt.time(15, 30))


def relogin_is_overdue(
    now: dt.datetime, last_success: dt.date | None, calendar: TradingCalendar | None = None
) -> bool:
    """True when today's broker login is DUE and has not happened.

    The catch-up check the FastAPI lifespan runs after `scheduler.start()`,
    and the fix for a failure that already cost a live session: on
    2026-08-05 the 08:40 cron did not fire, quotes returned HTTP 500 "Failed
    to fetch LTP" all morning, and it took a hand-run `POST /api/engine/
    relogin-broker` at 08:55 to recover. `should_start_recorder_now` above
    exists for exactly the same reason, found on 2026-07-30 — a once-a-day
    cron plus a process that restarts is a job that silently never runs.

    Two distinct ways the cron loses:

    * the process was not up at 08:40 (a redeploy, a crash, a laptop asleep),
      so nothing fired and `BackgroundScheduler` will not revisit it until
      tomorrow
    * the scheduler was busy at 08:40 and APScheduler dropped the run as a
      misfire — its default grace is one second

    `last_success` is what keeps this from becoming the thing the original
    design refused to build. The module docstring's objection to a
    restart-triggered relogin was that a dev `--reload` loop would replay
    real credentials against Angel's rate limiter on every reload; gating on
    "a login already succeeded today" means the second and every later
    restart is a no-op, so that objection no longer applies.

    `calendar` is optional for the same reason as `should_start_recorder_now`
    above — it keeps the pure weekday form available to tests and to any
    caller with no DB — but a live special session (Budget-day Saturday
    trading, Diwali Muhurat) needs the broker logged in same as any other
    session, and weekday arithmetic alone would never trigger it.
    """
    ist_now = now.astimezone(IST)
    if calendar is not None:
        if not calendar.is_trading_day(ist_now.date(), exchange="NSE"):
            return False
    elif ist_now.weekday() >= 5:  # no session, no broker to log in to
        return False
    if last_success == ist_now.date():
        return False
    return is_market_open(ist_now, _RELOGIN_WINDOW)


def relogin_catchup_due(session_factory: sessionmaker[Session], now: dt.datetime) -> bool:
    """`relogin_is_overdue`, with the stored last-success read for you and a
    missing table treated as "do not act".

    The lifespan runs before/independently of migrations, against a DB whose
    tables the API's own `create_all` has not necessarily touched — the same
    situation `te.engine.trading_calendar.get_calendar` documents and
    handles. Reading a bookkeeping key must never stop the app booting.

    Answering FALSE on an unreadable table is the safe direction, and not
    merely the convenient one: if `engine_state` cannot be read then success
    cannot be WRITTEN either, so a login fired here would be un-recordable
    and would therefore fire again on the very next restart. That is the
    credential-replay loop this whole design is built to avoid. The 08:40
    cron still covers the normal case.
    """
    try:
        with session_factory() as session:
            last_success = get_last_broker_relogin_day(session)
            calendar = get_calendar(session)
    except OperationalError:
        logger.warning("engine_state is not queryable yet — skipping the broker-relogin catch-up")
        return False
    return relogin_is_overdue(now, last_success, calendar)


def run_openalgo_relogin(settings: Settings, session_factory: sessionmaker[Session] | None = None) -> LoginResult:
    """The daily OpenAlgo-app + Angel-broker relogin (see
    `te.broker.openalgo_login`). Returns a `LoginResult` rather than raising
    — matches `_run_instrument_sync`'s isolated-failure convention, and lets
    `POST /api/engine/relogin-broker` (the ad-hoc same-day trigger) report
    the real reason back to the caller instead of a bare 500.

    `session_factory` is optional so every existing caller and test keeps
    working untouched; when given, a SUCCESSFUL login stamps today's date via
    `set_last_broker_relogin_day`, which is what `relogin_is_overdue` reads.
    Only success is recorded — a failed attempt must leave the job still due,
    or one transport error would suppress every retry for the rest of the
    day."""
    app_username = settings.openalgo_app_username
    app_password = settings.openalgo_app_password
    angel_client_id = settings.angel_client_id
    angel_pin = settings.angel_pin
    angel_totp_secret = settings.angel_totp_secret
    if (
        app_username is None
        or app_password is None
        or angel_client_id is None
        or angel_pin is None
        or angel_totp_secret is None
    ):
        return LoginResult(False, "not_configured", "openalgo relogin skipped: credentials not configured")

    try:
        result = login_openalgo(
            settings.openalgo_host,
            app_username=app_username,
            app_password=app_password.get_secret_value(),
            angel_client_id=angel_client_id,
            angel_pin=angel_pin.get_secret_value(),
            angel_totp_secret=angel_totp_secret.get_secret_value(),
        )
    except Exception:
        logger.exception("openalgo relogin failed with a transport error")
        return LoginResult(False, "transport", "request to OpenAlgo failed — see logs")

    if result.ok:
        logger.info("openalgo relogin succeeded")
        if session_factory is not None:
            with session_scope(session_factory) as session:
                set_last_broker_relogin_day(session, dt.datetime.now(IST).date())
    else:
        logger.warning("openalgo relogin failed", stage=result.stage, message=result.message)
    return result


def _current_premium_from_bars(store: BarStore, as_of: dt.datetime) -> Callable[[OpenPositionRow], Paise | None]:
    """The scheduled paper-cycle job's `current_premium` for `run_exit_cycle`
    — the latest CLOSED bar (via `bars_asof`, never a direct `BarStore.read`)
    for the position's symbol.

    Returns `None` when no bar is visible at `as_of`. It previously returned
    the position's own ENTRY premium, described as "a neutral, non-crashing
    mark" — but there is no such thing as a neutral mark. Reporting entry as
    the current price reads as "unchanged", which silently disables every
    price-based exit and reports a P&L of exactly zero. `None` means unknown,
    and the caller decides what unknown implies."""

    def _current_premium(row: OpenPositionRow) -> Paise | None:
        df = bars_asof(store, row.symbol, as_of, lookback=dt.timedelta(minutes=5))
        if df.empty:
            return None
        last_close = float(df.iloc[-1]["c"])
        return Paise(int(round(last_close * 100)))

    return _current_premium


def _current_premium_from_quotes(
    rest_client: OpenAlgoRestClient, store: BarStore, as_of: dt.datetime
) -> Callable[[OpenPositionRow], Paise | None]:
    """Marks open positions from a LIVE quote on the position's own symbol,
    falling back to bars, then to `None` (unknown).

    Necessary because open positions are option contracts, and the WS
    recorder only subscribes to the four index spot symbols
    (`NSE_UNDERLYINGS`/`BSE_UNDERLYINGS`) — chosen dynamically per signal,
    an option contract has no recorded bars at all. Without this, every
    option position would mark at its own entry premium forever, so no
    stop/target/trailing exit could ever fire and positions would only ever
    close on the time exit.

    Marks at the **bid**, not the LTP. The position is long the option, so
    the price that matters is the one it could actually be SOLD at; LTP is
    the last trade at either side of the book and systematically flatters
    both the mark-to-market and the recorded exit fill. Falls back to LTP
    only when the book carries no usable bid.

    Polling one quote per open position per cycle (a handful per minute) is
    deliberately preferred over widening the WS subscription:
    `OpenAlgoWSClient.subscribe()` only takes effect on the next reconnect,
    so a contract chosen mid-session would not stream until then."""
    from_bars = _current_premium_from_bars(store, as_of)
    # Memoised for this closure's lifetime, which is exactly one cycle
    # (`as_of` is fixed at construction). Two things depend on it:
    #
    # 1. The entry cycle prices every open position through
    #    `unrealized_pnl_paise` to decide whether to halt, and the exit cycle
    #    then prices the SAME rows to decide whether to close. Without a
    #    cache that is two blocking REST round trips per position per minute,
    #    and `_post` builds a fresh client per call, so the second is a full
    #    connect/request/close rather than a pooled reuse.
    # 2. Those two cycles must agree. Quotes taken a second apart differ, so
    #    an uncached source could halt on one mark and exit on another within
    #    the same minute — which the caller's comment already claimed could
    #    not happen.
    cache: dict[tuple[str, str], Paise | None] = {}

    def _current_premium(row: OpenPositionRow) -> Paise | None:
        key = (row.symbol, row.exchange)
        if key in cache:
            return cache[key]
        try:
            quote = rest_client.quotes(row.symbol, row.exchange)
        except OpenAlgoRestError:
            logger.warning("quote failed for open position; falling back to bars", symbol=row.symbol)
            marked = from_bars(row)
        else:
            if quote.bid > 0:
                marked = Paise(int(round(quote.bid * 100)))
            elif quote.ltp > 0:
                marked = Paise(int(round(quote.ltp * 100)))
            else:
                marked = from_bars(row)
        cache[key] = marked
        return marked

    return _current_premium


def _exit_geometry(settings: Settings) -> ExitGeometry:
    """Percentages of premium when configured, else the historic absolute
    distances — as ONE object, so the two forms can never both be live.

    Previously both were passed to `CycleConfig` and resolved per use site,
    which meant setting `paper_cycle_trailing_pct=None` to disable the trail
    silently fell back to `paper_cycle_trailing_distance_paise=300` — a Rs 3
    absolute trail, 3.68% of that day's Rs 81.50 NIFTY premium, and the same
    trail that had closed 14 of 14 trades on `trailing_stop` at a 3.1-minute
    average hold."""
    # Rupee risk wins when set: it is the most specific statement of intent
    # available (an exact rupee figure, not a percentage that resolves to a
    # different rupee figure per contract), so a config carrying both should
    # honour the exact one rather than the derived one.
    if settings.paper_cycle_max_loss_per_trade_paise is not None:
        return RupeeRiskGeometry(
            max_loss_paise=settings.paper_cycle_max_loss_per_trade_paise,
            target_multiple=settings.paper_cycle_target_risk_multiple,
            trailing_pct=settings.paper_cycle_trailing_pct,
            profit_lock_activation_pct=settings.paper_cycle_profit_lock_activation_pct,
            profit_lock_buffer_pct=settings.paper_cycle_profit_lock_buffer_pct,
        )
    if settings.paper_cycle_stop_pct is not None and settings.paper_cycle_target_pct is not None:
        return PremiumPercentGeometry(
            stop_pct=settings.paper_cycle_stop_pct,
            target_pct=settings.paper_cycle_target_pct,
            trailing_pct=settings.paper_cycle_trailing_pct,
            profit_lock_activation_pct=settings.paper_cycle_profit_lock_activation_pct,
            profit_lock_buffer_pct=settings.paper_cycle_profit_lock_buffer_pct,
        )
    return AbsolutePointGeometry(
        stop_distance=Paise(settings.paper_cycle_stop_distance_paise),
        target_distance=Paise(settings.paper_cycle_target_distance_paise),
        trailing_distance=(
            Paise(settings.paper_cycle_trailing_distance_paise)
            if settings.paper_cycle_trailing_distance_paise is not None
            else None
        ),
    )


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
        exit_geometry=_exit_geometry(settings),
        max_hold=dt.timedelta(minutes=settings.paper_cycle_max_hold_minutes),
        hard_exit_by=settings.paper_cycle_hard_exit_by,
        min_minutes_before_hard_exit=settings.paper_cycle_min_minutes_before_hard_exit,
        risk_limits=RiskLimitsConfig(
            max_daily_loss_paise=Paise(settings.paper_cycle_max_daily_loss_paise),
            max_concurrent_positions=settings.paper_cycle_max_concurrent_positions,
            max_trades_per_day=settings.paper_cycle_max_trades_per_day,
            max_consecutive_losses=settings.paper_cycle_max_consecutive_losses,
        ),
        max_entries_per_underlying_per_day=settings.paper_cycle_max_entries_per_underlying_per_day,
        max_lots=settings.paper_cycle_max_lots,
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
    - **kill switch gate** — a halt blocks `run_entry_cycle` only;
      `run_exit_cycle` still runs (matching its own contract — it is not
      halt-gated, since exiting is risk-REDUCING). Found live: this used to
      skip the run entirely, which meant tripping the kill switch — the
      exact event meant to protect capital — abandoned every open position
      with no stop-loss/trailing-stop/time-exit management for the rest of
      the halt, no human runbook actually watching it, contradicting this
      project's non-negotiable "every position always has a working exit
      plan" rule. A throttle (not a halt) does NOT block entries either —
      `te.engine.cycle.run_entry_cycle` already reduces size for a
      throttled cycle.
    - **session gate** — a no-op outside `te.domain.clock`'s session window
      (never a hardcoded literal).
    - **pause gate** — `te.engine.state.get_run_state`; `"paused"` skips
      the run entirely, same as a halt. This is the fix for a real gap: the
      dashboard's Pause/Resume buttons used to flip a display-only field
      nothing here ever read (see the plan's "Dashboard<->engine wiring
      remediation", Tier 2) — pausing did not pause anything.

    `config` is a TEMPLATE, not what actually runs: every call to
    `run_once` rebuilds the live cycle config from `te.engine.state`'s
    `AccountGuardrails` (capital, risk-per-trade, and the three
    `RiskLimitsConfig` fields), read fresh from the DB — see the plan's
    Tier 1. An operator who never saves anything on the Settings page gets
    exactly `config`'s env-var-derived values every time, unchanged from
    before this existed.
    """

    session_factory: sessionmaker[Session]
    store: BarStore
    charge_rate_table: ChargeRateTable
    config: CycleConfig
    max_orders_per_second: int
    settings: Settings
    #: Which exchange's holiday list decides whether today is a session.
    #: NSE and BSE share every holiday in the 2026 calendar, so one is
    #: enough — but it is named rather than assumed, because they have
    #: diverged before (BSE observes some Maharashtra dates NSE does not).
    calendar_exchange: str = "NSE"
    clock: Callable[[], dt.datetime] = _now_ist
    #: Turns the rule's index breakout into a real option contract. `None`
    #: trades the raw configured symbol at the rule's own price — correct
    #: only when that symbol is already an option (tests/backtests), never
    #: live. `build_scheduler` always supplies one.
    contract_resolver: ContractResolver | None = None
    #: Used to mark open OPTION positions, which have no recorded bars. See
    #: `_current_premium_from_quotes`. `None` falls back to bar-based marks.
    rest_client: OpenAlgoRestClient | None = None
    #: Called once per entry cycle for the current shadow-stage `MLHook`, or
    #: `None` when no model has been trained yet — see
    #: `te.ml.nightly.ShadowHookProvider`. A callable rather than a hook so a
    #: model registered by tonight's training job is picked up tomorrow
    #: without restarting the engine.
    #:
    #: This cannot influence trading. At `shadow` stage
    #: `MaturityGate.influence` returns `MLInfluence(1, False, None)` for any
    #: probability, and `run_entry_cycle` additionally swallows any exception
    #: the hook raises. Its entire effect is to write rows to
    #: `ml_predictions`, which is what later training reads.
    ml_hook_provider: Callable[[], MLHook | None] | None = None
    #: Not an init argument — always starts empty and is rewritten by
    #: `run_once`. `GET /api/engine/scheduler-status` reads it directly.
    status: PaperCycleStatus = field(default_factory=PaperCycleStatus, init=False)

    def run_once(self) -> None:
        as_of = self.clock()
        local = as_of.astimezone(IST)

        # Calendar gate FIRST, before the time-of-day window. A holiday is
        # not "outside the session" — it has no session — and the two were
        # indistinguishable while the engine only knew weekday arithmetic:
        # every exchange holiday ran a full day of cycles against a feed
        # that would never produce a bar, logging ordinary-looking skips.
        #
        # The window itself comes from the calendar, because Diwali Muhurat
        # trading is a real ~1-hour EVENING session on a date the exchange
        # is otherwise shut. Hardcoding 09:15-15:30 would idle through all
        # of it.
        with self.session_factory() as session:
            calendar = get_calendar(session)
        window = calendar.session_window(local.date(), exchange=self.calendar_exchange)
        if window is None:
            reason = "skipped_not_a_trading_day" if calendar.known else "skipped_calendar_unknown"
            self.status = PaperCycleStatus(last_run_at=as_of, last_result=reason)
            logger.info("paper cycle skipped", reason=reason, as_of=as_of.isoformat())
            return
        if not is_market_open(local, window):
            self.status = PaperCycleStatus(last_run_at=as_of, last_result="skipped_outside_session")
            logger.debug("paper cycle skipped: outside session window", as_of=as_of.isoformat())
            return

        with self.session_factory() as session:
            mode = get_mode(session)
            run_state = get_run_state(session)
        if mode != "dry-run":
            self.status = PaperCycleStatus(last_run_at=as_of, last_result="skipped_mode_live")
            logger.info("paper cycle skipped: engine mode is live, not dry-run", as_of=as_of.isoformat())
            return

        # Pause blocks NEW ENTRIES only — exits still run, exactly as the
        # kill-switch branch below does, and for the same reason: abandoning
        # open positions violates this project's non-negotiable "every
        # position always has a working exit plan" rule. Pausing is an
        # operator saying "stop opening trades", never "stop protecting the
        # ones I already have". Before this, `paused` returned here, above
        # `run_exit_cycle`, so a single dashboard click silently disabled
        # every stop-loss, target, trailing stop and the 15:20 hard exit —
        # leaving positions to run unmanaged into the close and overnight.
        paused = run_state == "paused"
        halted = paused
        if paused:
            logger.info(
                "paper cycle: engine paused — new entries blocked, exits still run (exiting is risk-reducing)",
                as_of=as_of.isoformat(),
            )

        with self.session_factory() as session:
            try:
                check_killswitch(session)
            except KillSwitchTrippedError:
                halted = True
                logger.warning(
                    "paper cycle: kill switch tripped — new entries blocked, "
                    "exits still run (exiting is risk-reducing)",
                    as_of=as_of.isoformat(),
                )

        with self.session_factory() as session:
            guardrails = get_guardrails(session, defaults=guardrails_defaults_from_settings(self.settings))
            # Read in the SAME session as the guardrails so the capital and
            # the anchor it is paired with can never come from either side
            # of a concurrent save.
            capital_set_at = get_capital_set_at(session)
            selections = get_instrument_selections(
                session, defaults=instrument_selections_defaults_from_settings(self.settings)
            )
        instrument_configs = tuple(
            InstrumentConfig(symbol=s.symbol, exchange=s.exchange, lot_size=s.lot_size) for s in selections if s.active
        )
        config = replace(
            self.config,
            capital=guardrails.capital,
            capital_set_at=capital_set_at,
            risk_budget_pct=guardrails.risk_per_trade_pct,
            max_position_size_pct=guardrails.max_position_size_pct,
            risk_limits=RiskLimitsConfig(
                max_daily_loss_paise=guardrails.max_daily_loss,
                max_concurrent_positions=guardrails.max_concurrent_positions,
                max_trades_per_day=guardrails.max_trades_per_day,
                max_drawdown_pct=guardrails.max_drawdown_pct,
                # Not an `AccountGuardrails` field: it is a behavioural
                # stand-down, not an account limit, and is deliberately not
                # dashboard-editable. Comes from the startup config template
                # so it survives a guardrails edit.
                max_consecutive_losses=self.config.risk_limits.max_consecutive_losses,
            ),
            instrument_configs=instrument_configs,
        )

        cost_model, execution = build_paper_execution_stack(
            self.session_factory,
            charge_rate_table=self.charge_rate_table,
            max_orders_per_second=self.max_orders_per_second,
            on=as_of.date(),
        )

        # ONE premium source for both cycles: the entry cycle's risk gates
        # price open positions to decide whether to halt, and the exit cycle
        # prices them to decide whether to close. Those two must never see
        # different marks for the same position in the same minute.
        premium_source = (
            _current_premium_from_quotes(self.rest_client, self.store, as_of)
            if self.rest_client is not None
            else _current_premium_from_bars(self.store, as_of)
        )

        if not halted:
            run_entry_cycle(
                session_factory=self.session_factory,
                store=self.store,
                execution=execution,
                cost_model=cost_model,
                config=config,
                as_of=as_of,
                contract_resolver=self.contract_resolver,
                current_premium=premium_source,
                ml_hook=self.ml_hook_provider() if self.ml_hook_provider is not None else None,
            )
        run_exit_cycle(
            session_factory=self.session_factory,
            execution=execution,
            cost_model=cost_model,
            current_premium=premium_source,
            as_of=as_of,
        )
        # `paused` and `halted` both block entries and both still run exits,
        # but they stay distinguishable in the status: one is an operator
        # choice to stop trading, the other is a risk breaker that tripped.
        if paused:
            result = "skipped_paused_entries_only"
        elif halted:
            result = "skipped_halted_entries_only"
        else:
            result = "ran"
        self.status = PaperCycleStatus(last_run_at=as_of, last_result=result)
        logger.debug("paper cycle ran", as_of=as_of.isoformat(), halted=halted, paused=paused)


#: Which `AccountGuardrails` fields actually have a `Settings.paper_cycle_*`
#: env var backing them, and that var's name. `max_position_size_pct`/
#: `max_drawdown_pct` are deliberately excluded: they have no env-var
#: equivalent at all (`guardrails_defaults_from_settings` seeds both from the
#: hard ceiling, not from `settings`), so a stored value differing from that
#: ceiling is not an ignored env var and must not be reported as one.
_GUARDRAIL_ENV_VARS: tuple[tuple[str, str], ...] = (
    ("capital", "TE_PAPER_CYCLE_CAPITAL_PAISE"),
    ("max_daily_loss", "TE_PAPER_CYCLE_MAX_DAILY_LOSS_PAISE"),
    ("max_trades_per_day", "TE_PAPER_CYCLE_MAX_TRADES_PER_DAY"),
    ("max_concurrent_positions", "TE_PAPER_CYCLE_MAX_CONCURRENT_POSITIONS"),
    ("risk_per_trade_pct", "TE_PAPER_CYCLE_RISK_BUDGET_PCT"),
)


def _warn_if_stored_guardrails_diverge_from_settings(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    """`PaperCycleRunner.run_once` (and every guardrails read in this
    module) always prefers the DB-stored guardrail over `Settings.
    paper_cycle_*` — see `get_guardrails`. Once ANYTHING has been saved from
    the dashboard, the corresponding `TE_PAPER_CYCLE_*` env var is silently
    ignored for trading: it only ever seeds a brand-new database (see
    `guardrails_defaults_from_settings`). Confirmed live:
    `TE_PAPER_CYCLE_CAPITAL_PAISE` was set to Rs 20,000 in
    `docker-compose.yml` while a stored Rs 50,000 governed every real paper
    cycle, and nothing said so.

    Runs once at scheduler build time (process startup), not per-cycle —
    this is a boot-time loud-and-clear check for an operator reading logs,
    not a repeating one. A missing `engine_state` table (a brand-new DB
    before the API's `create_all` has run) is treated the same way
    `relogin_catchup_due` treats it: skip silently, never crash the boot.
    """
    try:
        with session_factory() as session:
            stored = get_guardrails(session, defaults=guardrails_defaults_from_settings(settings))
    except OperationalError:
        logger.warning("engine_state is not queryable yet — skipping the startup guardrails mismatch check")
        return

    seeded = clamp_guardrails(guardrails_defaults_from_settings(settings))
    for field_name, env_var in _GUARDRAIL_ENV_VARS:
        stored_value = getattr(stored, field_name)
        seeded_value = getattr(seeded, field_name)
        if stored_value != seeded_value:
            logger.warning(
                f"guardrail mismatch at startup: the STORED value governs live trading, the {env_var} env var "
                "does not — it only seeds a brand-new database",
                guardrail=field_name,
                env_var=env_var,
                settings_value=str(seeded_value),
                stored_value=str(stored_value),
            )


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
    rest_client = OpenAlgoRestClient(
        host=settings.openalgo_host,
        api_key=settings.openalgo_api_key.get_secret_value(),
        quotes_per_second=settings.max_quotes_per_second,
    )
    # Used verbatim — see `Settings.openalgo_ws_host` for why this is a
    # separate, explicitly-configured endpoint and never derived from
    # `openalgo_host`.
    ws_client = OpenAlgoWSClient(url=settings.openalgo_ws_host, api_key=settings.openalgo_api_key.get_secret_value())
    ws_client.subscribe(
        [Instrument(exchange=exch, symbol=symbol) for symbol, exch in (*NSE_UNDERLYINGS, *BSE_UNDERLYINGS)]
    )
    recorder = BarRecorder(bar_store)
    # ONE resolver shared by the recorder and the paper cycle, so both read
    # the same broker expiry chain out of the same per-day cache.
    contract_resolver = OptionContractResolver(
        rest_client,
        offset=settings.paper_cycle_option_offset,
        max_spread_pct=settings.paper_cycle_max_spread_pct,
        min_premium_paise=settings.paper_cycle_min_premium_paise,
    )
    supervisor = WSRecorderSupervisor(
        ws_client,
        recorder,
        late_instruments=partial(_option_instruments, contract_resolver, settings),
    )

    session_factory = make_session_factory(engine)
    _warn_if_stored_guardrails_diverge_from_settings(settings, session_factory)
    charge_rate_table = load_charge_rate_table(settings.charges_path)
    paper_cycle_runner = PaperCycleRunner(
        session_factory=session_factory,
        store=bar_store,
        charge_rate_table=charge_rate_table,
        config=_default_cycle_config(settings),
        max_orders_per_second=settings.max_orders_per_second,
        settings=settings,
        contract_resolver=contract_resolver,
        rest_client=rest_client,
        ml_hook_provider=ShadowHookProvider(session_factory, bar_store),
    )

    scheduler = BackgroundScheduler(timezone=IST)
    scheduler.add_job(
        run_openalgo_relogin,
        args=[settings, session_factory],
        trigger=CronTrigger(hour=8, minute=40, day_of_week="mon-fri", timezone=IST),
        id="openalgo_relogin",
        replace_existing=True,
        # APScheduler's default grace is ONE SECOND: if the scheduler thread
        # is busy at 08:40 the run is dropped and not revisited until
        # tomorrow. That is one of the two ways 2026-08-05's login was lost.
        # 25 minutes keeps a late fire useful (instrument sync is 09:05) and
        # still refuses one so late it would race the session.
        misfire_grace_time=25 * 60,
        coalesce=True,
    )
    # Before the recorder and well before the first entry cycle: a halt left
    # over from yesterday's daily loss limit must be gone by the time the
    # cycle asks whether it may trade. See `reset_daily_loss_halt` — this
    # clears ONLY that one halt, never a breaker that needs a human.
    scheduler.add_job(
        reset_daily_loss_halt,
        args=[session_factory],
        trigger=CronTrigger(hour=9, minute=5, day_of_week="mon-fri", timezone=IST),
        id="daily_loss_halt_reset",
        replace_existing=True,
    )
    scheduler.add_job(
        supervisor.start,
        trigger=CronTrigger(hour=9, minute=10, day_of_week="mon-fri", timezone=IST),
        id="ws_recorder_start",
        replace_existing=True,
    )
    # 20:00, well after the 15:35 recorder stop, so the day's evaluations and
    # their outcome bars are all recorded before anything trains on them.
    # Weeknights only: there is nothing new to learn from on a Saturday, and
    # re-training on an unchanged sample would inflate the trial count DSR
    # deflates against for no new information.
    scheduler.add_job(
        run_nightly_training_job,
        args=[session_factory, engine, bar_store, charge_rate_table, settings],
        trigger=CronTrigger(hour=20, minute=0, day_of_week="mon-fri", timezone=IST),
        id="ml_nightly_training",
        replace_existing=True,
        # Training takes minutes and must never stack up behind itself.
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        supervisor.stop,
        trigger=CronTrigger(hour=15, minute=35, day_of_week="mon-fri", timezone=IST),
        id="ws_recorder_stop",
        replace_existing=True,
    )
    # Re-asks the broker for today's strikes while the recorder runs. This
    # is the retry: on 2026-08-03 the engine came up before the OpenAlgo
    # gateway, the single start-time resolution failed against a refused
    # connection, and premium recording stayed off for the whole session
    # until a human restarted it. It is also the drift correction — the
    # at-the-money strike follows the index all day — and the expiry
    # rollover. `refresh_late_instruments` no-ops unless the recorder is
    # actually running, so this stays quiet outside the session and on
    # holidays, when `supervisor.start` never fired.
    scheduler.add_job(
        supervisor.refresh_late_instruments,
        trigger=CronTrigger(hour="9-15", minute="*/5", day_of_week="mon-fri", timezone=IST),
        id="ws_late_subscription_refresh",
        replace_existing=True,
        # One slow resolution must not stack up behind the next tick.
        max_instances=1,
        coalesce=True,
    )
    # Catches the class of bug `ws_late_subscription_refresh` cannot: a
    # relogin (or any other event that kills the broker side of the feed
    # without dropping THIS process's WS connection) leaves `is_running()`
    # `True` with zero ticks arriving, silently, for as long as nobody
    # checks by hand — see `FeedHealthStatus`. Same 5-minute cadence as the
    # late-subscription refresh so a dead feed is caught and self-healed
    # within one cycle, not a whole session.
    scheduler.add_job(
        supervisor.check_feed_health,
        trigger=CronTrigger(hour="9-15", minute="*/5", day_of_week="mon-fri", timezone=IST),
        id="ws_feed_health_check",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _run_bhavcopy_ingest,
        args=[engine],
        # Hourly 19:00-23:00 rather than once at 18:30. NSE publishes its
        # F&O bhavcopy around 20:00 IST, so the old single early attempt
        # 404'd and gave up — losing 2026-08-03's NIFTY/BANKNIFTY premiums
        # until they were re-fetched by hand, and, going by the same 404
        # elsewhere in the ingest log, quietly losing days before that.
        # `_run_bhavcopy_ingest` skips whichever exchange already succeeded,
        # so the later runs cost nothing once the file is in.
        trigger=CronTrigger(hour="19-23", minute=0, day_of_week="mon-fri", timezone=IST),
        id="bhavcopy_ingest",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _run_instrument_sync,
        args=[rest_client, engine],
        trigger=CronTrigger(hour=8, minute=45, day_of_week="mon-fri", timezone=IST),
        id="instrument_sync",
        replace_existing=True,
    )
    scheduler.add_job(
        run_session_audit,
        args=[session_factory, settings],
        # 15:35 IST: after the 15:30 close and after the 15:15 hard exit, so
        # every position the day opened has resolved and nothing is still
        # moving. `misfire_grace_time` is generous because a missed audit is
        # a missed report — the one thing that must not happen quietly.
        trigger=CronTrigger(hour=15, minute=35, day_of_week="mon-fri", timezone=IST),
        id="session_audit",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30 * 60,
    )
    scheduler.add_job(
        _run_calendar_refresh,
        args=[session_factory, rest_client],
        # Weekly, not daily: the list changes a handful of times a year, and
        # every trading-day decision falls back to the STORED calendar
        # anyway. Sunday 08:00 so a revision published over the weekend is
        # in place before Monday's open. Runs on Sunday deliberately — the
        # one job here that must NOT be `mon-fri`, since it is what tells
        # the rest of the engine which of those days are real.
        trigger=CronTrigger(day_of_week="sun", hour=8, minute=0, timezone=IST),
        id="trading_calendar_refresh",
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
