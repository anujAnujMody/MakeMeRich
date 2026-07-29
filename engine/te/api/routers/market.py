import datetime as dt

from fastapi import APIRouter, Query, Response

from te.api.provenance import set_provenance
from te.api.schemas.dashboard import DailyPnL, EquityPoint, MarketSession, WatchlistItem
from te.api.schemas.trading import MarketData

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


@router.get("/market-status", response_model=MarketSession)
def get_market_status(response: Response) -> MarketSession:
    """Current exchange session state. Reports `closed` until a broker/
    market-data connection can confirm otherwise."""
    set_provenance(response, not_ready_reason="phase-0: no broker/market-data connection yet")
    return MarketSession(
        status="closed",
        label="Unknown",
        nextEvent="",
        currentTime=dt.datetime.now(dt.UTC).isoformat(),
    )
