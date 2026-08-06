"""te.execution.halt — the DB-flag halt layer. Extends the Phase 0
`engine_state` table (no schema change needed: it's already a generic
key/value table)."""

from __future__ import annotations

from pathlib import Path

import pytest

from te.execution.halt import (
    clear_halt,
    clear_throttle,
    is_halted,
    is_throttled,
    set_halt,
    set_throttle,
    throttle_reason,
)
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base


@pytest.fixture
def session_factory(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_not_halted_by_default(session_factory) -> None:
    with session_factory() as session:
        assert is_halted(session) is False


def test_not_throttled_by_default(session_factory) -> None:
    with session_factory() as session:
        assert is_throttled(session) is False


def test_set_throttle_persists_independently_of_halt(session_factory) -> None:
    with session_factory() as session:
        set_throttle(session, "tier 0 slippage breach")
        session.commit()

    with session_factory() as session:
        assert is_throttled(session) is True
        assert throttle_reason(session) == "tier 0 slippage breach"
        assert is_halted(session) is False  # throttle and halt are independent flags


def test_clear_throttle(session_factory) -> None:
    with session_factory() as session:
        set_throttle(session, "test throttle")
        session.commit()
    with session_factory() as session:
        clear_throttle(session)
        session.commit()
    with session_factory() as session:
        assert is_throttled(session) is False


def test_set_halt_persists(session_factory) -> None:
    with session_factory() as session:
        set_halt(session, "overfill on coid-1")
        session.commit()

    with session_factory() as session:
        assert is_halted(session) is True


def test_clear_halt(session_factory) -> None:
    with session_factory() as session:
        set_halt(session, "test halt")
        session.commit()
    with session_factory() as session:
        clear_halt(session)
        session.commit()
    with session_factory() as session:
        assert is_halted(session) is False
