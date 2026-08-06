import datetime as dt

from fastapi import APIRouter, Response

from te.api.db import session_factory
from te.api.provenance import set_provenance
from te.api.schemas.approval import ApprovalDecidePayload, PendingApproval
from te.domain.money import Paise, rupees
from te.engine.approvals import decide as decide_approval
from te.engine.approvals import pending_approvals
from te.persistence.db import session_scope
from te.persistence.models import ApprovalRow

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


def _to_schema(row: ApprovalRow) -> PendingApproval:
    return PendingApproval(
        id=row.id,
        createdAt=row.created_at.isoformat(),
        expiresAt=row.expires_at.isoformat(),
        instrument=row.instrument,
        side=row.side,
        lots=row.lots,
        premium=float(rupees(Paise(row.premium_paise))),
        stopLoss=float(rupees(Paise(row.stop_paise))),
        target=float(rupees(Paise(row.target_paise))),
        estimatedCost=float(rupees(Paise(row.estimated_cost_paise))),
        modelVerdict=row.model_verdict,
        status=row.status,
    )


@router.get("", response_model=list[PendingApproval])
def list_approvals(response: Response) -> list[PendingApproval]:
    """Trade approvals still awaiting a human decision and not yet expired.
    Reads real persisted rows — provenance is `paper` whenever any exist."""
    now = dt.datetime.now(dt.UTC)
    with session_scope(session_factory) as session:
        rows = pending_approvals(session, now=now)

    if rows:
        set_provenance(response, provenance="paper", sample_size=len(rows), not_ready_reason="")
    else:
        set_provenance(response, provenance="none", sample_size=0, not_ready_reason="no pending approvals right now")
    return [_to_schema(row) for row in rows]


@router.post("/decide", response_model=PendingApproval | None)
def decide_approval_endpoint(payload: ApprovalDecidePayload, response: Response) -> PendingApproval | None:
    """Records a human approve/reject decision against one pending approval,
    returning the updated row (or `None` if no approval has that id)."""
    now = dt.datetime.now(dt.UTC)
    with session_scope(session_factory) as session:
        row = decide_approval(session, payload.id, payload.decision, now=now)

    if row is None:
        set_provenance(
            response, provenance="none", sample_size=0, not_ready_reason=f"no approval with id={payload.id!r}"
        )
        return None
    set_provenance(response, provenance="paper", sample_size=1, not_ready_reason="")
    return _to_schema(row)
