"""Which strategies are switched ON, persisted in `engine_state`.

Follows the same key/value pattern as mode, run-state and the trading
calendar: read fresh on every check, so a change from the dashboard takes
effect on the next cycle with no restart.

### Why the default is `orb` alone, and not "everything"

Thirty-two strategies are registered so they can be MEASURED. None of them
has beaten a coin flip on 2.5 years of real option premiums — the best
luck-adjusted score in the library is 0.035 against a bar of 0.95 — so
enabling them by default would put the engine into 32 simultaneous losing
strategies the moment it started.

`orb` is the default because it is what the engine already runs; this module
changes what is CONFIGURABLE, not what happens if nobody configures anything.

### Why the control can never be enabled

`random_entry` exists to be the yardstick every other strategy is measured
against. `set_strategy_enabled` refuses it outright rather than trusting the
UI to hide the switch — a coin flip that could be armed by a stray click is
not a control, it is a liability.
"""

from __future__ import annotations

import json

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from te.engine.state import upsert_engine_state
from te.persistence.models import EngineState
from te.strategy.registry import available, spec

_KEY = "enabled_strategies_json"

#: What runs when nothing has been configured — the engine's existing
#: behaviour, unchanged.
DEFAULT_ENABLED = frozenset({"orb"})


def enabled_strategies(session: Session) -> frozenset[str]:
    """The set of enabled strategy names.

    Falls back to `DEFAULT_ENABLED` when nothing is stored, and on a missing
    table — the same fail-safe posture as `get_calendar`, so a fresh database
    behaves exactly like the engine did before this module existed.
    """
    try:
        row = session.get(EngineState, _KEY)
    except OperationalError:
        return DEFAULT_ENABLED
    if row is None or not row.value:
        return DEFAULT_ENABLED
    try:
        names = json.loads(row.value)
    except json.JSONDecodeError:
        return DEFAULT_ENABLED
    if not isinstance(names, list):
        return DEFAULT_ENABLED
    # Unknown names are dropped rather than kept: a strategy can be renamed
    # or removed between releases, and carrying a dead name forward would
    # make the engine look configured for something that cannot run.
    return frozenset(n for n in names if isinstance(n, str) and n in set(available()))


def set_strategy_enabled(session: Session, name: str, *, enabled: bool) -> frozenset[str]:
    """Switches one strategy on or off. Does not commit — the caller's
    transaction, matching every other setter in `te.engine.state`."""
    if name not in set(available()):
        raise ValueError(f"unknown strategy {name!r} (available: {available()})")
    if enabled and not spec(name).tradeable:
        raise ValueError(
            f"{name!r} is measure-only and can never be enabled — it is the control every other "
            "strategy is scored against, not a strategy"
        )
    current = set(enabled_strategies(session))
    if enabled:
        current.add(name)
    else:
        current.discard(name)
    upsert_engine_state(session, _KEY, json.dumps(sorted(current)))
    return frozenset(current)
