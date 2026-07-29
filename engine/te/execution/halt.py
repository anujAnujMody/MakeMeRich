"""The halt mechanism — a persisted flag `ExecutionManager`/`Reconciler` set,
and that `submit()` checks and refuses to act on. Reuses Phase 0's
`engine_state` key/value table (no migration needed) rather than adding a
dedicated table, per the plan's scope note for this phase: full kill-switch
layering (in-process/DB-flag/broker-side) is `te/risk/killswitch.py`'s job in
a later phase; execution correctness only needs the DB-flag layer directly.

**Phase 7 addition — the throttle state.** `te/risk/monitors.py`'s Tier 0
(slippage) and Tier 1 (CUSUM) monitors need a state distinct from a full
halt: "reduce position size" rather than "refuse every new order". Modeled
as two more keys in the SAME `engine_state` key/value table (still no
migration needed — this table was already schema-free), symmetric with
`is_halted`/`set_halt`/`clear_halt` above. `is_halted` and `is_throttled`
are independent — a halt always wins in practice (nothing gets sized at all
once halted) but the two flags are tracked separately so the dashboard can
show "throttled" without implying "halted".
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from te.engine.state import upsert_engine_state as _upsert
from te.persistence.models import EngineState

_HALT_KEY = "halted"
_HALT_REASON_KEY = "halt_reason"
_THROTTLE_KEY = "throttled"
_THROTTLE_REASON_KEY = "throttle_reason"


def is_halted(session: Session) -> bool:
    row = session.get(EngineState, _HALT_KEY)
    return row is not None and row.value == "true"


def halt_reason(session: Session) -> str | None:
    row = session.get(EngineState, _HALT_REASON_KEY)
    return row.value if row is not None else None


def set_halt(session: Session, reason: str) -> None:
    """Sets the halt flag AND its reason. Does not commit — callers own the
    transaction boundary (matches every other `te.persistence` writer)."""
    _upsert(session, _HALT_KEY, "true")
    _upsert(session, _HALT_REASON_KEY, reason)


def clear_halt(session: Session) -> None:
    _upsert(session, _HALT_KEY, "false")
    _upsert(session, _HALT_REASON_KEY, "")


def is_throttled(session: Session) -> bool:
    row = session.get(EngineState, _THROTTLE_KEY)
    return row is not None and row.value == "true"


def throttle_reason(session: Session) -> str | None:
    row = session.get(EngineState, _THROTTLE_REASON_KEY)
    return row.value if row is not None else None


def set_throttle(session: Session, reason: str) -> None:
    """Sets the throttle flag AND its reason. Does not commit — callers own
    the transaction boundary. Distinct from `set_halt`: a throttle reduces
    size, it never blocks `submit()` by itself."""
    _upsert(session, _THROTTLE_KEY, "true")
    _upsert(session, _THROTTLE_REASON_KEY, reason)


def clear_throttle(session: Session) -> None:
    _upsert(session, _THROTTLE_KEY, "false")
    _upsert(session, _THROTTLE_REASON_KEY, "")

