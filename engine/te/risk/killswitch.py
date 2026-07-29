"""The kill switch — 3 layers, per the plan:

1. **In-process** — a fast in-memory flag checked before every `submit()`,
   zero DB round-trip. Cheapest, fastest circuit breaker; goes cold on
   process restart, which is why layer 2 exists.
2. **DB flag** — `te.execution.halt`'s persisted `engine_state` row.
   Survives a restart; `ExecutionManager.submit()` already checks it
   directly, and `check()` here checks it too so a caller that only goes
   through this module still gets the same guarantee.
3. **Broker square-off runbook** — OPERATIONAL, MANUAL, not code, per the
   plan's stated scope for this phase. If layers 1+2 have tripped and a
   human needs to be certain no exposure remains at the broker regardless of
   what this process thinks its positions are: log into the broker
   dashboard/OpenAlgo UI directly (NOT via this engine's own API, which may
   itself be compromised/wrong) and manually square off every open F&O
   position and cancel every open order for the trading account. This is
   the last-resort layer precisely because it does not trust this codebase
   at all — automating it would defeat the point.

**Phase 7 addition — `throttle()`/`is_throttled()`.** `te/risk/monitors.py`'s
Tier 0 (slippage) and Tier 1 (CUSUM) monitors need a state weaker than a
full halt: "reduce position size", not "refuse every order". This reuses
the SAME DB-flag mechanism (`te.execution.halt`'s throttle keys, added
alongside the halt keys in the same schema-free `engine_state` table) rather
than inventing a second halt mechanism, per the plan's explicit instruction.
There is no in-process throttle cache — a throttle is read fresh from the DB
by `te.risk.sizing` (a later call site) each cycle, since unlike a halt it
doesn't need to block a network call before that read can happen; keeping
throttle DB-only avoids adding a second in-process global for what is, at
worst, a one-cycle-late size reduction rather than a missed halt.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from te.execution.halt import halt_reason, is_halted, set_halt, set_throttle

# The throttle READ side is `te.execution.halt`'s verbatim — re-exported
# here under this module's naming so risk-layer callers have one import
# point, rather than wrapped in a function that only renames it.
from te.execution.halt import is_throttled as is_currently_throttled
from te.execution.halt import throttle_reason as current_throttle_reason

_tripped_in_process = False


class KillSwitchTrippedError(RuntimeError):
    pass


def trip(session: Session, reason: str) -> None:
    """Trips BOTH layers: the in-process flag immediately, and the DB flag
    (via `te.execution.halt.set_halt`) so the trip survives a restart.
    Does not commit — callers own the transaction boundary."""
    global _tripped_in_process
    _tripped_in_process = True
    set_halt(session, reason)


def throttle(session: Session, reason: str) -> None:
    """Sets the (DB-only, see module docstring) throttle flag — a monitor's
    "reduce size" action, distinct from `trip()`'s "refuse every order".
    Does not commit — callers own the transaction boundary."""
    set_throttle(session, reason)


def reset_in_process_cache() -> None:
    """Clears ONLY the in-process flag — call at process startup (a cold
    cache is the honest starting state; the DB flag is what carries a real
    halt across the restart) and by tests that simulate a restart."""
    global _tripped_in_process
    _tripped_in_process = False


def is_tripped_in_process() -> bool:
    return _tripped_in_process


def check(session: Session) -> None:
    """Raises `KillSwitchTrippedError` if EITHER layer indicates a halt. Callers
    (e.g. `te.engine.cycle` before calling `ExecutionManager.submit`) should
    call this first — it's a pure in-memory check unless the in-process
    layer is cold, in which case it falls through to one DB read."""
    if _tripped_in_process:
        raise KillSwitchTrippedError("kill switch tripped (in-process layer)")
    if is_halted(session):
        raise KillSwitchTrippedError(f"kill switch tripped (DB flag layer): {halt_reason(session) or ''}")


__all__ = [
    "KillSwitchTrippedError",
    "check",
    "current_throttle_reason",
    "is_currently_throttled",
    "is_tripped_in_process",
    "reset_in_process_cache",
    "throttle",
    "trip",
]
