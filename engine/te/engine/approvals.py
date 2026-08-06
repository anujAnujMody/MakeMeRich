"""`PendingApproval` lifecycle + TTL expiry — backs the live-money
per-trade approval gate (`/api/approvals`). Persisted via `ApprovalRow`
(`te/persistence/models.py`), so a pending approval survives a restart the
same way an open position does.

TTL expiry is evaluated lazily, on read (`pending_approvals()`) and on
decide (`decide()`) — there is no background sweep in this phase; every
caller that reads/decides approvals gets the truth as of `now` regardless.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from te.domain.clock import assume_utc as _as_utc
from te.domain.clock import to_utc as _utc
from te.domain.money import Paise
from te.persistence.models import ApprovalRow

ApprovalDecision = Literal["approve", "reject"]
ModelVerdict = Literal["favorable", "neutral", "caution"]

DEFAULT_TTL = dt.timedelta(minutes=5)


@dataclass(frozen=True)
class ApprovalRequest:
    instrument: str
    side: Literal["BUY", "SELL"]
    lots: int
    premium: Paise
    stop_loss: Paise
    target: Paise
    estimated_cost: Paise
    model_verdict: ModelVerdict


def create_approval(
    session: Session, request: ApprovalRequest, *, now: dt.datetime, ttl: dt.timedelta = DEFAULT_TTL
) -> ApprovalRow:
    row = ApprovalRow(
        id=str(uuid.uuid4()),
        created_at=_utc(now),
        expires_at=_utc(now + ttl),
        instrument=request.instrument,
        side=request.side,
        lots=request.lots,
        premium_paise=int(request.premium),
        stop_paise=int(request.stop_loss),
        target_paise=int(request.target),
        estimated_cost_paise=int(request.estimated_cost),
        model_verdict=request.model_verdict,
        status="pending",
    )
    session.add(row)
    session.flush()
    return row


def expire_stale_approvals(session: Session, *, now: dt.datetime) -> int:
    """Marks every still-`pending` approval whose TTL has passed as
    `expired`. Returns how many were expired. Does not commit."""
    rows = session.execute(select(ApprovalRow).where(ApprovalRow.status == "pending")).scalars().all()
    expired = 0
    for row in rows:
        if _as_utc(row.expires_at) <= _utc(now):
            row.status = "expired"
            expired += 1
    return expired


def pending_approvals(session: Session, *, now: dt.datetime) -> list[ApprovalRow]:
    expire_stale_approvals(session, now=now)
    return list(session.execute(select(ApprovalRow).where(ApprovalRow.status == "pending")).scalars().all())


def decide(session: Session, approval_id: str, decision: ApprovalDecision, *, now: dt.datetime) -> ApprovalRow | None:
    """Returns the updated row, or `None` if `approval_id` is unknown. A
    decision on an already-expired (or already-decided) approval is a
    no-op that returns the row as-is — TTL expiry always wins over a late
    decision."""
    row = session.get(ApprovalRow, approval_id)
    if row is None:
        return None
    if row.status != "pending":
        return row
    if _as_utc(row.expires_at) <= _utc(now):
        row.status = "expired"
        return row
    row.status = "approved" if decision == "approve" else "rejected"
    return row
