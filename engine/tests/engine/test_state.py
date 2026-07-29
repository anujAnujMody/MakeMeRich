from __future__ import annotations

from pathlib import Path

import pytest

from te.engine.state import get_mode, get_run_state, set_mode, set_run_state
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'state_test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_mode_defaults_to_dry_run(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        assert get_mode(session) == "dry-run"


def test_run_state_defaults_to_paused(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        assert get_run_state(session) == "paused"


def test_mode_persists_across_restart(session_factory, tmp_path: Path) -> None:  # noqa: ANN001
    with session_factory() as session:
        set_mode(session, "live")
        session.commit()

    with session_factory() as session:
        assert get_mode(session) == "live"

    engine2 = make_engine(f"sqlite:///{tmp_path / 'state_test.db'}")
    from te.persistence.db import make_session_factory as _msf

    restarted = _msf(engine2)
    with restarted() as session:
        assert get_mode(session) == "live"


def test_set_mode_rejects_unknown_value(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session, pytest.raises(ValueError, match="unknown mode"):
        set_mode(session, "banana")  # type: ignore[arg-type]


def test_run_state_round_trips(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        set_run_state(session, "running")
        session.commit()
    with session_factory() as session:
        assert get_run_state(session) == "running"
