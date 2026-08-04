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
_HALT_KIND_KEY = "halt_kind"
_THROTTLE_KEY = "throttled"
_THROTTLE_REASON_KEY = "throttle_reason"

#: The one halt that is scoped to a single SESSION rather than to a problem
#: someone has to look at. Everything else that halts — the drawdown
#: breaker, an overfill, a reconciliation mismatch, the kill switch — means
#: "a human must decide", and must survive until a human does.
DAILY_LOSS_HALT = "daily_loss"


def is_halted(session: Session) -> bool:
    row = session.get(EngineState, _HALT_KEY)
    return row is not None and row.value == "true"


def halt_reason(session: Session) -> str | None:
    row = session.get(EngineState, _HALT_REASON_KEY)
    return row.value if row is not None else None


def set_halt(session: Session, reason: str, *, kind: str = "") -> None:
    """Sets the halt flag AND its reason. Does not commit — callers own the
    transaction boundary (matches every other `te.persistence` writer).

    `kind` is a machine-readable label, distinct from the human `reason`,
    used by `clear_daily_halt` to tell a per-session halt from one that must
    outlive the session. It defaults to empty precisely so that any caller
    which does NOT label its halt is treated as "needs a human" — the safe
    direction. Matching on the reason TEXT instead was the obvious
    alternative and was rejected: a reason string is prose meant for an
    operator, and a reworded message would silently change which halts clear
    themselves overnight."""
    _upsert(session, _HALT_KEY, "true")
    _upsert(session, _HALT_REASON_KEY, reason)
    _upsert(session, _HALT_KIND_KEY, kind)


def halt_kind(session: Session) -> str:
    """The machine-readable label of the CURRENT halt, or empty when none
    was recorded. Empty also covers halts written before this key existed,
    which therefore never auto-clear — again the safe direction."""
    row = session.get(EngineState, _HALT_KIND_KEY)
    return row.value if row is not None else ""


def clear_halt(session: Session) -> None:
    _upsert(session, _HALT_KEY, "false")
    _upsert(session, _HALT_REASON_KEY, "")
    _upsert(session, _HALT_KIND_KEY, "")


def clear_daily_halt(session: Session) -> bool:
    """Clears the halt ONLY if it was the daily-loss one. Returns whether it
    cleared, so the caller can log/record the fact.

    A daily loss limit is by definition scoped to one day, but nothing ever
    cleared it: no scheduled job called `clear_halt`, only the
    reset-drawdown-breaker endpoint did. So the first session to breach the
    limit blocked every entry indefinitely until somebody noticed and
    clicked a button — during a 1-2 month unattended paper run that silently
    costs weeks of data, and the outage looks identical to "the strategy had
    no signals".

    Deliberately narrow: a drawdown breach, an overfill, a reconciliation
    mismatch and the kill switch all still require a human, because none of
    those is a per-day condition."""
    if not is_halted(session) or halt_kind(session) != DAILY_LOSS_HALT:
        return False
    clear_halt(session)
    return True


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

