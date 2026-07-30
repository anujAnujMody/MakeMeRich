"""Read-side lookups over the `instruments` table (`te.persistence.models.
Instrument`, synced daily by `te.broker.instrument_sync`) — the only place
a real, broker-verified lot size for an underlying is resolved from, per
the plan's repeated rule that lot sizes must never be literals in code.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from te.persistence.models import Instrument


def latest_lot_size(session: Session, *, underlying: str, instrument_type: str = "FUT") -> int | None:
    """The most recently synced lot size for `underlying` (e.g. `"NIFTY"`,
    matching `Instrument.name` — the underlying, not the full contract
    symbol), or `None` if nothing has been synced yet. Futures are used
    because lot size is identical across every FUT/CE/PE contract in the
    same underlying+expiry series (see
    `te.engine.scheduler._fno_lot_size_contracts`'s docstring) and a future
    needs no strike to exist."""
    row = session.execute(
        select(Instrument)
        .where(Instrument.name == underlying, Instrument.instrument_type == instrument_type)
        .order_by(Instrument.updated_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row.lot_size if row is not None else None
