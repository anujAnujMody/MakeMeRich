import datetime as dt

from fastapi import APIRouter, Response
from sqlalchemy.orm import Session

from te.api.db import bar_store, session_factory, settings
from te.api.provenance import set_provenance
from te.api.routers.positions import position_from_row
from te.api.schemas.dashboard import DashboardData, DashboardSnapshot, OpenPosition, PipelineStageInfo
from te.api.trade_stats import summarize_trades
from te.data.asof import latest_close_paise
from te.domain.clock import IST
from te.domain.money import Paise, rupees
from te.engine.state import (
    PIPELINE_STAGE_KEYS,
    get_guardrails,
    get_last_cycle_pipeline,
    get_mode,
    get_run_state,
    guardrails_defaults_from_settings,
)
from te.ml.gates import MaturityGate
from te.persistence.repos.paper_trading import (
    daily_net_pnl_paise,
    open_positions,
    recent_session_dates,
    trades_closed_since,
    trades_count_today,
    trades_today,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

#: A "week" here means the 5 most recent SESSION dates with a closed trade
#: (matches `recent_session_dates`'s documented purpose), not a calendar
#: week — a calendar week can span a weekend/holiday gap that would silently
#: undercount trading days.
_TRAILING_SESSIONS = 5

_PIPELINE_STAGE_LABELS: dict[str, str] = {
    "fetch": "Fetch",
    "analyze": "Analyze",
    "risk": "Risk",
    "decide": "Decide",
    "act": "Act",
}


def _format_duration_ms(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


def _build_pipeline(session: Session) -> list[PipelineStageInfo]:
    """The most recently completed `run_entry_cycle`'s real, measured
    per-stage timing (`te.engine.state.get_last_cycle_pipeline`) — `[]`
    until the first cycle has run. A stage that genuinely didn't fire this
    cycle (e.g. every instrument skipped before sizing, so `decide`/`act`
    never ran) shows `pending`, never fabricated as `done`."""
    last = get_last_cycle_pipeline(session)
    if last is None:
        return []
    return [
        PipelineStageInfo(
            key=key,
            label=_PIPELINE_STAGE_LABELS[key],
            state="done" if last.stages[key].reached else "pending",
            durationLabel=_format_duration_ms(last.stages[key].elapsed_ms) if last.stages[key].reached else None,
        )
        for key in PIPELINE_STAGE_KEYS
        if key in last.stages
    ]


def _next_check_in_seconds(session: Session, *, now: dt.datetime, run_state: str) -> int:
    """Seconds until the paper cycle is next due — `last cycle's as_of +
    interval - now`, floored at 0.

    `0` is honest here and means "nothing is scheduled or it is already
    due": the engine is paused, or no cycle has ever run. It is NOT a
    countdown that happens to have reached zero on a running engine, because
    a running engine always has a stored last-cycle timestamp within one
    interval. Previously hardcoded `0`, which rendered as a permanently
    stalled countdown regardless of what the scheduler was doing."""
    if run_state != "running":
        return 0
    last = get_last_cycle_pipeline(session)
    if last is None:
        return 0
    due = last.as_of + dt.timedelta(minutes=settings.paper_cycle_interval_minutes)
    return max(0, int((due - now).total_seconds()))


@router.get("", response_model=DashboardData)
def get_dashboard(response: Response) -> DashboardData:
    """Headline dashboard tiles — day P&L, win rate, open positions, quotes
    — from the real `trades`/`open_positions` tables.

    `quotes` needs a live quote join this endpoint doesn't do yet — stays
    `[]`, unchanged from before."""
    as_of = dt.datetime.now(IST)
    today = as_of.date()
    with session_factory() as session:
        today_rows = trades_today(session, today)
        summary = summarize_trades(today_rows)
        positions = open_positions(session)
        guardrails = get_guardrails(session, defaults=guardrails_defaults_from_settings(settings))

    has_data = bool(today_rows) or bool(positions)
    set_provenance(response, provenance="paper" if has_data else "none", sample_size=summary.total_trades)
    capital = float(rupees(guardrails.capital))
    return DashboardData(
        dayPnl=summary.total_pnl,
        dayPnlPercent=(summary.total_pnl / capital * 100) if capital else 0,
        winRate=summary.win_rate,
        totalTrades=summary.total_trades,
        activePositions=len(positions),
        positions=[
            position_from_row(
                row,
                current_premium=Paise(
                    latest_close_paise(bar_store, row.symbol, as_of, fallback=row.entry_premium_paise)
                ),
                on=today,
            )
            for row in positions
        ],
        quotes=[],
    )


@router.get("/snapshot", response_model=DashboardSnapshot)
def get_dashboard_snapshot(response: Response) -> DashboardSnapshot:
    """Single-call snapshot of engine mode, risk budget usage, open positions
    and the trailing-session rollup — from real persisted state. `pipeline`
    is the most recently completed cycle's real, measured per-stage timing
    (see `_build_pipeline`)."""
    as_of = dt.datetime.now(IST)
    today = as_of.date()
    with session_factory() as session:
        mode = get_mode(session)
        run_state = get_run_state(session)
        today_pnl = daily_net_pnl_paise(session, today)
        today_trade_count = trades_count_today(session, today)
        positions = open_positions(session)
        guardrails = get_guardrails(session, defaults=guardrails_defaults_from_settings(settings))
        pipeline = _build_pipeline(session)
        next_check_in_seconds = _next_check_in_seconds(session, now=as_of, run_state=run_state)

        session_dates = recent_session_dates(session, limit=_TRAILING_SESSIONS)
        week_rows = trades_closed_since(session, min(session_dates)) if session_dates else []
        week_summary = summarize_trades(week_rows)

    set_provenance(
        response,
        provenance="paper" if (today_trade_count or positions) else "none",
        sample_size=today_trade_count,
    )
    return DashboardSnapshot(
        mode=mode,
        status="live" if run_state == "running" else "paused",
        asOf=as_of.astimezone(dt.UTC).isoformat(),
        todayPnl=float(rupees(today_pnl)),
        dailyLossLimit=float(rupees(guardrails.max_daily_loss)),
        openPositionsCount=len(positions),
        maxPositions=guardrails.max_concurrent_positions,
        tradesToday=today_trade_count,
        maxTradesPerDay=guardrails.max_trades_per_day,
        positions=[
            OpenPosition(
                symbol=row.symbol,
                lots=row.lots,
                entryTime=row.opened_at.isoformat(),
                pnl=position_from_row(
                    row,
                    current_premium=Paise(
                        latest_close_paise(bar_store, row.symbol, as_of, fallback=row.entry_premium_paise)
                    ),
                    on=today,
                ).unrealisedPnl,
            )
            for row in positions
        ],
        pipeline=pipeline,
        weekWinRatePct=week_summary.win_rate,
        weekTrades=week_summary.total_trades,
        weekNetPnl=week_summary.total_pnl,
        # The REAL ladder position, read from `ml_maturity_state` — not the
        # literal `"shadow"` this used to send. Those agreed only because
        # nothing has been promoted yet; the moment a promotion happened the
        # dashboard would have kept reporting `shadow` while the model was
        # actually influencing decisions, which is the single most
        # safety-relevant number on this page.
        mlStage=MaturityGate(session_factory).current_stage().value,
        nextCheckInSeconds=next_check_in_seconds,
    )
