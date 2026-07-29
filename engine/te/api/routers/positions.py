from fastapi import APIRouter, Response

from te.api.provenance import set_provenance
from te.api.schemas.trading import Position, SquareOffPayload, SuccessResponse

router = APIRouter(prefix="/api/positions", tags=["positions"])


@router.get("", response_model=list[Position])
def list_positions(response: Response) -> list[Position]:
    """Open positions. Returns the honest empty list until the execution core
    is wired up — there are no real positions to report."""
    set_provenance(response, not_ready_reason="phase-0: execution core not wired yet, zero real positions")
    return []


@router.post("/squareoff", response_model=SuccessResponse)
def square_off_position(payload: SquareOffPayload, response: Response) -> SuccessResponse:
    """Squares off one position. Always reports `success=False` until the
    execution core is wired up — nothing can actually be squared off, and
    claiming otherwise would be a lie the caller might act on."""
    set_provenance(response, not_ready_reason="phase-0: execution core not wired yet")
    return SuccessResponse(success=False)
