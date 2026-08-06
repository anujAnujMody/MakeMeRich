import datetime as dt

from fastapi import APIRouter, Query, Response

from te.api.db import session_factory, settings
from te.api.provenance import set_provenance
from te.api.schemas.dashboard import DailyPnL, EquityPoint, MarketSession, WatchlistItem
from te.api.schemas.trading import MarketData
from te.domain.calendar import TradingCalendar
from te.domain.clock import DEFAULT_SESSION, IST, is_market_open
from te.domain.money import Paise, rupees
from te.engine.state import get_guardrails, guardrails_defaults_from_settings
from te.engine.trading_calendar import get_calendar
from te.persistence.repos.paper_trading import daily_pnl as repo_daily_pnl
from te.persistence.repos.paper_trading import equity_curve as repo_equity_curve

# Prefix is only `/api`: this router's paths (`/api/quotes`, `/api/history`,
# `/api/equity-curve`, `/api/daily-pnl`, `/api/watchlist`,
# `/api/market-status`) share no deeper segment, and the resolved URLs are a
# frozen contract.
router = APIRouter(prefix="/api", tags=["market"])


@router.get("/quotes", response_model=list[MarketData])
def get_quotes(response: Response, symbol: str | None = None, exchange: str | None = None) -> list[MarketData]:
    """Live quotes for the requested symbol(s). Empty until a broker/market-
    data connection exists."""
    set_provenance(response, not_ready_reason="phase-0: no broker/market-data connection yet")
    return []


@router.get("/history", response_model=list[MarketData])
def get_history(
    response: Response,
    symbol: str | None = None,
    exchange: str | None = None,
    interval: str | None = None,
) -> list[MarketData]:
    """Historical bars for one symbol at `interval`. Empty until the bar
    recorder is running.

    No MSW reference body existed for this endpoint; specced from the
    `MarketData[]` TS type alone (see plan's flagged endpoints)."""
    set_provenance(response, not_ready_reason="phase-0: no bar recorder running yet")
    return []


@router.get("/equity-curve", response_model=list[EquityPoint])
def get_equity_curve(
    response: Response,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
) -> list[EquityPoint]:
    """Real end-of-day account equity over the `from`..`to` window —
    `capital + cumulative realized net P&L`, reconstructed from closed
    trades (see `te.persistence.repos.paper_trading.equity_curve` for why
    there is no separate snapshot table to read from)."""
    with session_factory() as session:
        guardrails = get_guardrails(session, defaults=guardrails_defaults_from_settings(settings))
        curve = repo_equity_curve(
            session,
            capital_paise=int(guardrails.capital),
            from_=dt.date.fromisoformat(from_) if from_ else None,
            to=dt.date.fromisoformat(to) if to else None,
        )
    set_provenance(response, provenance="paper" if curve else "none", sample_size=len(curve))
    return [EquityPoint(date=day.isoformat(), value=float(rupees(Paise(equity)))) for day, equity in curve]


@router.get("/daily-pnl", response_model=list[DailyPnL])
def get_daily_pnl(response: Response, month: str | None = None) -> list[DailyPnL]:
    """Real per-day net P&L for the given `month` (`"YYYY-MM"`), or every day
    with a closed trade when `month` is omitted."""
    with session_factory() as session:
        rows = repo_daily_pnl(session, month=month)
    set_provenance(response, provenance="paper" if rows else "none", sample_size=len(rows))
    return [DailyPnL(date=day.isoformat(), pnl=float(rupees(Paise(pnl))), trades=count) for day, pnl, count in rows]


@router.get("/watchlist", response_model=list[WatchlistItem])
def get_watchlist(response: Response) -> list[WatchlistItem]:
    """Watchlist instruments with their latest quotes. Empty until a
    broker/market-data connection exists."""
    set_provenance(response, not_ready_reason="phase-0: no broker/market-data connection yet")
    return []


#: NSE decides the session for this engine's purposes — see
#: `PaperCycleRunner.calendar_exchange` for why it is named, not assumed.
_CALENDAR_EXCHANGE = "NSE"


def _compute_market_session(now_ist: dt.datetime, calendar: TradingCalendar) -> MarketSession:
    """Pure computation half of `get_market_status`, split out so tests can
    drive every branch (open/pre-open/closed/holiday/weekend) without
    monkeypatching `dt.datetime.now`.

    Every branch now consults the real broker-published holiday calendar
    rather than weekday arithmetic. The old version named the day after a
    holiday-Friday as "the next trading day" when it was a Saturday, and
    reported a full open session on Republic Day."""
    today = now_ist.date()
    window = calendar.session_window(today, exchange=_CALENDAR_EXCHANGE)

    if window is None:
        return _closed(now_ist, calendar, label=_closed_label(today, calendar))

    if is_market_open(now_ist, window):
        close = dt.datetime.combine(today, window.end, tzinfo=IST)
        return MarketSession(
            status="open",
            label="Market open",
            nextEvent=close.isoformat(),
            currentTime=now_ist.isoformat(),
        )

    if now_ist.timetz().replace(tzinfo=None) < window.start:
        open_ = dt.datetime.combine(today, window.start, tzinfo=IST)
        return MarketSession(
            status="pre-open",
            label="Market opens soon",
            nextEvent=open_.isoformat(),
            currentTime=now_ist.isoformat(),
        )

    return _closed(now_ist, calendar, label="Market closed")


def _closed_label(today: dt.date, calendar: TradingCalendar) -> str:
    if not calendar.known:
        return "Market status unavailable — no exchange calendar loaded"
    if today.weekday() >= 5:
        return "Market closed — weekend"
    return "Market closed — exchange holiday"


def _closed(now_ist: dt.datetime, calendar: TradingCalendar, *, label: str) -> MarketSession:
    """`nextEvent` is the next REAL session open, or `""` when the calendar
    cannot name one (nothing fetched, or the stored year has run out).
    Guessing "the next weekday" is what this change exists to stop — it
    named exchange holidays as trading days.

    Empty string rather than `None` because the dashboard contract types
    this field as a non-nullable `string` (`dashboard/src/types/index.ts`);
    `MarketClock.tsx` already renders it behind a falsy guard, so `""`
    displays nothing instead of a fabricated date."""
    nxt = calendar.next_trading_day(now_ist.date(), exchange=_CALENDAR_EXCHANGE)
    if nxt is None:
        return MarketSession(status="closed", label=label, nextEvent="", currentTime=now_ist.isoformat())
    window = calendar.session_window(nxt, exchange=_CALENDAR_EXCHANGE) or DEFAULT_SESSION
    next_open = dt.datetime.combine(nxt, window.start, tzinfo=IST)
    return MarketSession(
        status="closed", label=label, nextEvent=next_open.isoformat(), currentTime=now_ist.isoformat()
    )


@router.get("/market-status", response_model=MarketSession)
def get_market_status(response: Response) -> MarketSession:
    """Current exchange session state, from the real IST session window
    (`te.domain.clock`) and the broker-published exchange holiday calendar
    (`te.engine.trading_calendar`) — no live market-data connection needed,
    since this is calendar arithmetic, not a quote."""
    with session_factory() as session:
        calendar = get_calendar(session)
    if not calendar.known:
        set_provenance(response, not_ready_reason="no exchange holiday calendar has been fetched yet")
    else:
        set_provenance(response, provenance="paper", sample_size=1)
    return _compute_market_session(dt.datetime.now(IST), calendar)
