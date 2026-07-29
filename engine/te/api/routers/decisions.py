from fastapi import APIRouter, Response

from te.api.provenance import set_provenance
from te.api.schemas.dashboard import CycleEvaluation

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


@router.get("/today", response_model=list[CycleEvaluation])
def get_today_decisions(response: Response) -> list[CycleEvaluation]:
    """Every condition-by-condition strategy evaluation recorded today.
    Empty until an engine cycle has run."""
    set_provenance(response, not_ready_reason="phase-0: no engine cycle has run yet")
    return []
