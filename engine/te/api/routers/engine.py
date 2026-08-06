import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Request, Response
from sqlalchemy.orm import Session

from te.api.db import bar_store, charge_rate_table, session_factory, settings
from te.api.provenance import set_provenance
from te.api.schemas.ops import EngineHealthStatus, SchedulerJobStatus, SchedulerStatus
from te.api.schemas.settings import (
    AccountGuardrailsPayload,
    InstrumentSelectionPayload,
    InstrumentSelectionsPayload,
    ReloginResponse,
)
from te.domain.clock import IST
from te.domain.costs import CostModel, select_rates
from te.domain.money import Paise, rupees
from te.engine.cycle import unrealized_pnl_paise
from te.engine.scheduler import run_openalgo_relogin
from te.engine.state import (
    AccountGuardrails,
    InstrumentSelection,
    get_capital_set_at,
    get_guardrails,
    get_instrument_selections,
    get_peak_equity_paise,
    get_run_state,
    guardrails_defaults_from_settings,
    instrument_selections_defaults_from_settings,
    set_guardrails,
    set_instrument_selections,
    set_run_state,
)
from te.execution.halt import clear_halt, halt_reason, is_halted
from te.persistence.db import session_scope
from te.persistence.models import AuditLog
from te.persistence.repos.paper_trading import total_net_pnl_paise
from te.risk import killswitch

router = APIRouter(prefix="/api/engine", tags=["engine"])


@router.get("/scheduler-status", response_model=SchedulerStatus)
def get_scheduler_status(request: Request) -> SchedulerStatus:
    """Confirms the scheduler's jobs (including the Phase 4/6/7 paper-cycle
    job — see `te.engine.scheduler.PaperCycleRunner`) are actually
    registered, and reports the paper cycle job's last-run time/result.
    Reads `request.app.state.scheduler`/`.paper_cycle_runner`, set by
    `te.api.main`'s lifespan — `None` for both (an empty job list) only if
    read before the lifespan has started, which does not happen in normal
    operation."""
    scheduler = getattr(request.app.state, "scheduler", None)
    runner = getattr(request.app.state, "paper_cycle_runner", None)
    supervisor = getattr(request.app.state, "ws_supervisor", None)

    jobs = (
        [
            SchedulerJobStatus(id=job.id, nextRunTime=job.next_run_time, maxInstances=job.max_instances)
            for job in scheduler.get_jobs()
        ]
        if scheduler is not None
        else []
    )
    feed_health = supervisor.feed_health if supervisor is not None else None
    return SchedulerStatus(
        jobs=jobs,
        paperCycleLastRunAt=runner.status.last_run_at if runner is not None else None,
        paperCycleLastResult=runner.status.last_result if runner is not None else None,
        feedLastCheckedAt=feed_health.checked_at if feed_health is not None else None,
        feedLastTickAt=feed_health.last_tick_at if feed_health is not None else None,
    )


def _current_drawdown_pct(session: Session, *, capital: Paise, as_of: dt.datetime) -> float:
    """The SAME quantity `te.risk.limits.check_max_drawdown` breaches on:
    how far current equity has fallen below its ratcheting peak-equity
    watermark, in percent.

    Equity is built exactly as `te.engine.cycle.run_entry_cycle` builds it —
    `capital + lifetime realized net P&L + unrealized P&L on open positions`
    — because a displayed drawdown that disagreed with the one that trips
    the breaker would be worse than the `0.0` this replaces.

    Reads the watermark, never writes it: ratcheting is the trading loop's
    job, and a dashboard poll must not move a risk threshold. `0.0` when no
    watermark exists yet (nothing has traded) or equity is at a new high —
    both genuinely mean zero drawdown, unlike the hardcoded `0.0` before."""
    peak = get_peak_equity_paise(session)
    if peak is None or int(peak) <= 0:
        return 0.0
    unrealized = unrealized_pnl_paise(
        session,
        store=bar_store,
        cost_model=CostModel(select_rates(charge_rate_table, as_of.date())),
        as_of=as_of,
        # An option contract has no recorded bars (only the four index spot
        # symbols are subscribed), so the `store` fallback would resolve
        # every mark to the entry premium and report zero unrealized loss no
        # matter how far underwater the book was. `last_mark_paise` is the
        # most recent price the trading loop actually observed — the same
        # value its own exit checks fall back to.
        current_premium=lambda row: Paise(row.last_mark_paise) if row.last_mark_paise is not None else None,
    )
    # Same anchor the trading loop sizes and halts off (`te.engine.cycle`,
    # via `CycleConfig.capital_set_at`) — the dashboard must not report a
    # drawdown computed from a different equity than the breaker enforces.
    equity = int(capital) + int(total_net_pnl_paise(session, since=get_capital_set_at(session))) + int(unrealized)
    if equity >= int(peak):
        return 0.0
    return float(Decimal(int(peak) - equity) / Decimal(int(peak)) * Decimal(100))


def _current_health(request: Request) -> EngineHealthStatus:
    """Reads the REAL persisted state — `run_state` (`te.engine.state`), the
    real kill-switch halt flag (`te.execution.halt`, the same flag
    `PaperCycleRunner.run_once` checks before every cycle), and the real
    `max_drawdown_pct` guardrail — instead of the process-local
    `state.engine_health` struct the paper cycle never reads. Was a literal
    Phase-0 stub (see the plan's "Dashboard<->engine wiring remediation",
    Tier 2): Pause/Resume/Reset-breaker looked real but silently did
    nothing to the actual trading loop.

    `lastSuccessfulPollSecondsAgo` reads `PaperCycleRunner.status.
    last_run_at` off `request.app.state.paper_cycle_runner` (same source
    `get_scheduler_status` uses) — found live on 2026-07-30: this was
    hardcoded `0.0`, which is exactly the field that would have surfaced
    that day's real incident (non-atomic bar writes silently crashing the
    live paper cycle every minute for ~2h49m) — the dashboard would have
    shown a permanently-healthy engine throughout. Stays `None` only when
    the runner genuinely hasn't run yet (no cycle since process start)."""
    now_ist = dt.datetime.now(IST)
    with session_factory() as session:
        run_state = get_run_state(session)
        halted = is_halted(session)
        reason = (halt_reason(session) or "").lower()
        guardrails = get_guardrails(session, defaults=guardrails_defaults_from_settings(settings))
        drawdown_pct = _current_drawdown_pct(session, capital=guardrails.capital, as_of=now_ist)

    daily_loss_state = "normal"
    if halted and "daily loss limit" in reason:
        daily_loss_state = "halted_day"

    runner = getattr(request.app.state, "paper_cycle_runner", None)
    last_run_at = runner.status.last_run_at if runner is not None else None
    # The dashboard contract's field is a non-nullable `number` (see
    # dashboard/src/types/ops.ts) — `0.0` here means only "no cycle has run
    # yet this process" (a narrow just-after-startup window), never "the
    # engine is healthy"; once the first cycle runs this is always real.
    seconds_ago = (dt.datetime.now(dt.UTC) - last_run_at).total_seconds() if last_run_at is not None else 0.0

    return EngineHealthStatus(
        runState=run_state,
        dailyLossState=daily_loss_state,
        drawdownBreakerTripped=halted,
        currentDrawdownPct=drawdown_pct,
        maxDrawdownLimitPct=float(guardrails.max_drawdown_pct),
        lastSuccessfulPollSecondsAgo=seconds_ago,
    )


@router.get("/health", response_model=EngineHealthStatus)
def get_engine_health(request: Request, response: Response) -> EngineHealthStatus:
    """Engine run state, drawdown-breaker status and guardrail limit — from
    real persisted state (see `_current_health`)."""
    set_provenance(response, provenance="paper", sample_size=1)
    return _current_health(request)


@router.post("/pause", response_model=EngineHealthStatus)
def pause_engine(request: Request, response: Response) -> EngineHealthStatus:
    """Pauses the engine for real: `PaperCycleRunner.run_once` checks
    `get_run_state` on every scheduled run and skips entirely when
    `"paused"` — this is no longer a display-only flip (see the plan's
    Tier 2)."""
    with session_scope(session_factory) as session:
        set_run_state(session, "paused")
    set_provenance(response, provenance="paper", sample_size=1)
    return _current_health(request)


@router.post("/resume", response_model=EngineHealthStatus)
def resume_engine(request: Request, response: Response) -> EngineHealthStatus:
    """Resumes the engine for real — see `pause_engine`."""
    with session_scope(session_factory) as session:
        set_run_state(session, "running")
    set_provenance(response, provenance="paper", sample_size=1)
    return _current_health(request)


@router.post("/reset-drawdown-breaker", response_model=EngineHealthStatus)
def reset_drawdown_breaker(request: Request, response: Response) -> EngineHealthStatus:
    """Clears the REAL kill-switch halt — both the persisted DB flag
    (`te.execution.halt.clear_halt`) and the in-process cache
    (`te.risk.killswitch`'s fast layer 1, which `clear_halt` alone can't
    reach since it's a separate module-level flag in the SAME process — see
    `te.risk.killswitch`'s module docstring on the 3 layers). Previously
    reset a display-only field while the real halt/kill-switch had no API
    path to clear at all (see the plan's Tier 2)."""
    with session_scope(session_factory) as session:
        clear_halt(session)
    killswitch.reset_in_process_cache()
    set_provenance(response, provenance="paper", sample_size=1)
    return _current_health(request)


@router.post("/relogin-broker", response_model=ReloginResponse)
def relogin_broker(request: Request, response: Response) -> ReloginResponse:
    """Ad-hoc same-day OpenAlgo-app + Angel-broker relogin (see
    `te.engine.scheduler.run_openalgo_relogin`) — the scheduled 08:40 IST
    job covers the daily case, this covers recovering from an unplanned
    mid-session outage without waiting for tomorrow's cron or clicking
    through OpenAlgo's own web UI. Real credentials, real broker call, on
    every invocation — not something to poll or call speculatively.

    Bounces the WS recorder (stop, then start) when it's already running.
    Found live on 2026-08-04: OpenAlgo deletes its Angel WS broker adapter
    the moment a relogin lands ("force re-initialization with fresh
    credentials on next connection"), but this engine's WS client only
    re-authenticates on its OWN reconnect — and a relogin mid-session
    doesn't touch that connection. Every tick, index AND option, silently
    stopped until the next `supervisor.start()` (09:10 IST the next trading
    day) with nothing anywhere saying so. A restart here is what actually
    triggers the client to reconnect and OpenAlgo to recreate the adapter."""
    result = run_openalgo_relogin(settings)
    supervisor = getattr(request.app.state, "ws_supervisor", None)
    if result.ok and supervisor is not None and supervisor.is_running():
        supervisor.stop()
        supervisor.start()
    if not result.ok:
        set_provenance(response, not_ready_reason=result.message)
    else:
        set_provenance(response, provenance="paper", sample_size=1)
    return ReloginResponse(success=result.ok, stage=result.stage, message=result.message)


@router.get("/guardrails", response_model=AccountGuardrailsPayload)
def get_account_guardrails(response: Response) -> AccountGuardrailsPayload:
    """Live account guardrails (capital, risk limits) — `PaperCycleRunner`
    reads the exact same `get_guardrails` call on every cycle (see
    `te.engine.scheduler`), so what this shows is what's actually in effect,
    not a second copy that can drift. Falls back to `Settings.paper_cycle_*`
    env-var defaults when nothing has been saved yet."""
    with session_factory() as session:
        guardrails = get_guardrails(session, defaults=guardrails_defaults_from_settings(settings))
    set_provenance(response, provenance="paper", sample_size=1)
    return AccountGuardrailsPayload(
        capitalRupees=float(rupees(guardrails.capital)),
        maxDailyLossRupees=float(rupees(guardrails.max_daily_loss)),
        maxPositionSizePct=float(guardrails.max_position_size_pct),
        maxDrawdownPct=float(guardrails.max_drawdown_pct),
        maxTradesPerDay=guardrails.max_trades_per_day,
        maxConcurrentPositions=guardrails.max_concurrent_positions,
        riskPerTradePct=float(guardrails.risk_per_trade_pct),
    )


@router.put("/guardrails", response_model=AccountGuardrailsPayload)
def put_account_guardrails(payload: AccountGuardrailsPayload, response: Response) -> AccountGuardrailsPayload:
    """Saves live account guardrails — takes effect on the VERY NEXT
    scheduled paper cycle, no restart (see `PaperCycleRunner.run_once`).
    422s on invalid input via the payload schema's own field constraints;
    `set_guardrails` raises `ValueError` (-> 500) only if a value passes the
    schema but somehow fails the domain-level check too, which should not
    be reachable given the schema already enforces the same bounds."""
    guardrails = AccountGuardrails(
        capital=Paise(round(payload.capitalRupees * 100)),
        max_daily_loss=Paise(round(payload.maxDailyLossRupees * 100)),
        max_position_size_pct=_to_decimal(payload.maxPositionSizePct),
        max_drawdown_pct=_to_decimal(payload.maxDrawdownPct),
        max_trades_per_day=payload.maxTradesPerDay,
        max_concurrent_positions=payload.maxConcurrentPositions,
        risk_per_trade_pct=_to_decimal(payload.riskPerTradePct),
    )
    with session_scope(session_factory) as session:
        set_guardrails(session, guardrails)
        session.add(
            AuditLog(
                actor="dashboard",
                action="guardrails.update",
                detail=(
                    f"capital={payload.capitalRupees} maxDailyLoss={payload.maxDailyLossRupees} "
                    f"maxPositionSizePct={payload.maxPositionSizePct} maxDrawdownPct={payload.maxDrawdownPct} "
                    f"maxTradesPerDay={payload.maxTradesPerDay} "
                    f"maxConcurrentPositions={payload.maxConcurrentPositions} "
                    f"riskPerTradePct={payload.riskPerTradePct}"
                ),
            )
        )
    return get_account_guardrails(response)


def _to_decimal(value: float) -> Decimal:
    """Via `str()`, not `Decimal(value)` directly — a float's binary
    representation carries artifacts (`Decimal(35.5)` is
    `35.5000000000000071...`) that `str()` on the same float doesn't, since
    Python's float repr already rounds to the shortest string that
    round-trips."""
    return Decimal(str(value))


@router.get("/instruments", response_model=InstrumentSelectionsPayload)
def get_instruments(response: Response) -> InstrumentSelectionsPayload:
    """Live instrument selections — which symbols the paper cycle trades,
    each with its own exchange/lot size (see the plan's Tier 1
    "multi-instrument" fix: `CycleConfig` used to have one shared exchange/
    lot_size for every instrument, silently wrong for anything but a single
    NFO symbol). `PaperCycleRunner` reads this exact call on every cycle."""
    with session_factory() as session:
        selections = get_instrument_selections(session, defaults=instrument_selections_defaults_from_settings(settings))
    set_provenance(response, provenance="paper", sample_size=1)
    return InstrumentSelectionsPayload(
        instruments=[
            InstrumentSelectionPayload(symbol=s.symbol, exchange=s.exchange, lotSize=s.lot_size, active=s.active)
            for s in selections
        ]
    )


@router.put("/instruments", response_model=InstrumentSelectionsPayload)
def put_instruments(payload: InstrumentSelectionsPayload, response: Response) -> InstrumentSelectionsPayload:
    """Saves live instrument selections — takes effect on the VERY NEXT
    scheduled paper cycle, no restart. `set_instrument_selections` rejects
    duplicate `(symbol, exchange)` pairs and non-positive lot sizes (422
    only for the schema-level checks; a duplicate pair passes the schema
    but fails here, surfacing as a 500 — see the same reasoning as
    `put_account_guardrails`)."""
    selections = tuple(
        InstrumentSelection(symbol=i.symbol, exchange=i.exchange, lot_size=i.lotSize, active=i.active)
        for i in payload.instruments
    )
    with session_scope(session_factory) as session:
        set_instrument_selections(session, selections)
        session.add(
            AuditLog(
                actor="dashboard",
                action="instruments.update",
                detail=f"{len(selections)} instrument(s): "
                + ", ".join(f"{s.symbol}@{s.exchange}(lot={s.lot_size},active={s.active})" for s in selections),
            )
        )
    return get_instruments(response)
