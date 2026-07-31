import datetime as dt

from fastapi import APIRouter, Query, Response

from te.api.provenance import set_provenance
from te.api.schemas.dashboard import DailyPnL, EquityPoint, MarketSession, WatchlistItem
from te.api.schemas.trading import MarketData
from te.domain.clock import DEFAULT_SESSION, IST, is_market_open

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
    """Account equity over the `from`..`to` window. Empty until account
    snapshots are recorded."""
    set_provenance(response, not_ready_reason="phase-0: no account snapshots recorded yet")
    return []


@router.get("/daily-pnl", response_model=list[DailyPnL])
def get_daily_pnl(response: Response, month: str | None = None) -> list[DailyPnL]:
    """Per-day net P&L for the given `month`. Empty until closed trades are
    recorded."""
    set_provenance(response, not_ready_reason="phase-0: no closed trades recorded yet")
    return []


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
