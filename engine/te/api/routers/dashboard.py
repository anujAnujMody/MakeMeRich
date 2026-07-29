import datetime as dt

from fastapi import APIRouter, Response

from te.api.provenance import set_provenance
from te.api.schemas.dashboard import DashboardData, DashboardSnapshot

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardData)
def get_dashboard(response: Response) -> DashboardData:
    """Headline dashboard tiles — day P&L, win rate, open positions, quotes.
    All zero until trades and market data exist."""
    set_provenance(response)
    return DashboardData(
        dayPnl=0,
        dayPnlPercent=0,
        winRate=0,
        totalTrades=0,
        activePositions=0,
        positions=[],
        quotes=[],
    )


@router.get("/snapshot", response_model=DashboardSnapshot)
def get_dashboard_snapshot(response: Response) -> DashboardSnapshot:
    """Single-call snapshot of engine mode, risk budget usage, open positions
    and the week's rollup. All counters zero until the engine runs."""
    set_provenance(response)
    return DashboardSnapshot(
        mode="dry-run",
        status="paused",
        asOf=dt.datetime.now(dt.UTC).isoformat(),
        nextCheckInSeconds=0,
        todayPnl=0,
        dailyLossLimit=0,
        openPositionsCount=0,
        maxPositions=0,
        tradesToday=0,
        maxTradesPerDay=0,
        positions=[],
        pipeline=[],
        weekWinRatePct=0,
        weekTrades=0,
        weekNetPnl=0,
        mlStage="shadow",
    )
