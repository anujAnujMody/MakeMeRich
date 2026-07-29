from fastapi import APIRouter, Query, Response

from te.api.provenance import set_provenance
from te.api.schemas.trading import Trade

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("", response_model=list[Trade])
def list_trades(
    response: Response,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    strategy: str | None = None,
    symbol: str | None = None,
) -> list[Trade]:
    """Executed trades over the `from`..`to` window, optionally filtered by
    strategy or symbol. Empty until closed trades are recorded."""
    set_provenance(response, not_ready_reason="phase-0: no closed trades recorded yet")
    return []
