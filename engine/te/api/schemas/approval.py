"""Mirrors dashboard/src/types/approval.ts."""

from typing import Literal

from pydantic import BaseModel

# ONE definition of the verdict vocabulary — owned by `te.engine.approvals`,
# which produces it; this schema only re-exports it under the API's name so
# the wire contract can never drift from what the engine actually writes.
from te.engine.approvals import ModelVerdict as ApprovalVerdict

ApprovalStatus = Literal["pending", "approved", "rejected", "expired"]


class PendingApproval(BaseModel):
    id: str
    createdAt: str
    expiresAt: str
    instrument: str
    side: Literal["BUY", "SELL"]
    lots: int
    premium: float
    stopLoss: float
    target: float
    # Round-trip cost, priced off the TARGET-leg premium (higher STT than
    # the stop-leg) plus modelled slippage — never the optimistic estimate.
    estimatedCost: float
    modelVerdict: ApprovalVerdict
    status: ApprovalStatus


class ApprovalDecidePayload(BaseModel):
    id: str
    decision: Literal["approve", "reject"]
