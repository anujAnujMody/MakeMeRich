from fastapi import APIRouter, Query, Response

from te.api.provenance import set_provenance
from te.api.schemas.pnl import PnLAnalysis

router = APIRouter(prefix="/api/pnl", tags=["pnl"])


@router.get("", response_model=PnLAnalysis)
def get_pnl_analysis(
    response: Response,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
) -> PnLAnalysis:
    """P&L breakdown over the `from`..`to` window. All zero and `sharpe`
    stays `None` until closed trades exist — a Sharpe with no sample is
    exactly the fabricated-metric class this API refuses to emit."""
    set_provenance(response, not_ready_reason="phase-0: no closed trades recorded yet")
    return PnLAnalysis(
        totalPnl=0,
        winRate=0,
        totalTrades=0,
        winningTrades=0,
        losingTrades=0,
        avgWin=0,
        avgLoss=0,
        maxDrawdown=0,
        sharpe=None,
        period={"from": from_ or "", "to": to or ""},
    )
