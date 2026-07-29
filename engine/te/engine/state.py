"""Persisted `mode`/`run_state` — extends Phase 0's `engine_state`
key/value table (same one `te.execution.halt` already uses for the halt
flag) rather than adding a new table, so a restart can never silently reset
either back to a default."""

from __future__ import annotations

import datetime as dt
from typing import Literal, get_args

from sqlalchemy.orm import Session

from te.domain.clock import to_utc
from te.persistence.models import EngineState

Mode = Literal["dry-run", "live"]
RunState = Literal["running", "paused"]

_MODE_KEY = "mode"
_RUN_STATE_KEY = "run_state"
_PARAMS_FROZEN_AT_KEY = "params_frozen_at"

_DEFAULT_MODE: Mode = "dry-run"
_DEFAULT_RUN_STATE: RunState = "paused"


def upsert_engine_state(session: Session, key: str, value: str) -> None:
    """The ONE writer for the `engine_state` key/value table — shared with
    `te.execution.halt`, which stores its halt/throttle flags as keys in this
    same table (see that module's docstring for why it reuses this table
    rather than adding its own)."""
    row = session.get(EngineState, key)
    if row is None:
        session.add(EngineState(key=key, value=value))
    else:
        row.value = value
        row.updated_at = dt.datetime.now(dt.UTC)


def get_mode(session: Session) -> Mode:
    row = session.get(EngineState, _MODE_KEY)
    if row is None or row.value not in get_args(Mode):
        return _DEFAULT_MODE
    return row.value  # type: ignore[return-value]


def set_mode(session: Session, mode: Mode) -> None:
    """Does not commit — callers own the transaction boundary, matching
    `te.execution.halt`'s existing convention."""
    if mode not in get_args(Mode):
        raise ValueError(f"unknown mode {mode!r} — expected one of {get_args(Mode)}")
    upsert_engine_state(session, _MODE_KEY, mode)


def get_run_state(session: Session) -> RunState:
    row = session.get(EngineState, _RUN_STATE_KEY)
    if row is None or row.value not in get_args(RunState):
        return _DEFAULT_RUN_STATE
    return row.value  # type: ignore[return-value]


def set_run_state(session: Session, run_state: RunState) -> None:
    if run_state not in get_args(RunState):
        raise ValueError(f"unknown run_state {run_state!r} — expected one of {get_args(RunState)}")
    upsert_engine_state(session, _RUN_STATE_KEY, run_state)


def get_params_frozen_at(session: Session) -> dt.datetime | None:
    """When the active strategy's tunable parameters were last frozen —
    `None` means never frozen (or reset on the most recent param change, per
    the plan's R5). This is the anchor `te.risk.live_gate.LiveUnlockGate`
    counts post-freeze paper sessions from: "paper is OOS" only holds after
    this timestamp."""
    row = session.get(EngineState, _PARAMS_FROZEN_AT_KEY)
    if row is None or not row.value:
        return None
    return dt.datetime.fromisoformat(row.value)


def set_params_frozen_at(session: Session, ts: dt.datetime) -> None:
    """Resets on any param change (per the plan's R5) — callers own calling
    this again whenever tunable parameters are edited, not just on the
    first freeze."""
    upsert_engine_state(session, _PARAMS_FROZEN_AT_KEY, to_utc(ts, name="params_frozen_at").isoformat())
