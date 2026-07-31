import datetime as dt

from fastapi import APIRouter, Query, Response

from te.api.db import session_factory, settings
from te.api.provenance import set_provenance
from te.api.schemas.dashboard import DailyPnL, EquityPoint, MarketSession, WatchlistItem
from te.api.schemas.trading import MarketData
from te.domain.clock import DEFAULT_SESSION, IST, is_market_open
from te.domain.money import Paise, rupees
from te.engine.state import get_guardrails, guardrails_defaults_from_settings
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


def _next_trading_day(on: dt.date) -> dt.date:
    """Next weekday after `on` — a calendar approximation, not an NSE
    holiday calendar (none is wired up yet), so an exchange holiday will
    still show the following weekday as the next session."""
    nxt = on + dt.timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += dt.timedelta(days=1)
    return nxt


def _compute_market_session(now_ist: dt.datetime) -> MarketSession:
    """Pure computation half of `get_market_status`, split out so tests can
    drive every branch (open/pre-open/closed/weekend) without monkeypatching
    `dt.datetime.now`."""
    today = now_ist.date()

    if today.weekday() >= 5:
        next_open = dt.datetime.combine(_next_trading_day(today), DEFAULT_SESSION.start, tzinfo=IST)
        return MarketSession(
            status="closed",
            label="Market closed — weekend",
            nextEvent=next_open.isoformat(),
            currentTime=now_ist.isoformat(),
        )

    if is_market_open(now_ist):
        close = dt.datetime.combine(today, DEFAULT_SESSION.end, tzinfo=IST)
        return MarketSession(
            status="open",
            label="Market open",
            nextEvent=close.isoformat(),
            currentTime=now_ist.isoformat(),
        )

    if now_ist.timetz().replace(tzinfo=None) < DEFAULT_SESSION.start:
        open_ = dt.datetime.combine(today, DEFAULT_SESSION.start, tzinfo=IST)
        return MarketSession(
            status="pre-open",
            label="Market opens soon",
            nextEvent=open_.isoformat(),
            currentTime=now_ist.isoformat(),
        )

    next_open = dt.datetime.combine(_next_trading_day(today), DEFAULT_SESSION.start, tzinfo=IST)
    return MarketSession(
        status="closed",
        label="Market closed",
        nextEvent=next_open.isoformat(),
        currentTime=now_ist.isoformat(),
    )


@router.get("/market-status", response_model=MarketSession)
def get_market_status(response: Response) -> MarketSession:
    """Current exchange session state, computed from the real IST session
    window (`te.domain.clock`) — no broker connection required, since this
    is exchange-calendar arithmetic, not live market data."""
    set_provenance(response, not_ready_reason="computed from exchange calendar; no NSE holiday list wired up yet")
    return _compute_market_session(dt.datetime.now(IST))
