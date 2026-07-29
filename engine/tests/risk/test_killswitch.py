"""`te.risk.killswitch` — layer 1 (in-process) and layer 2 (DB flag).
Layer 3 (broker square-off runbook) is documented, not code, per this
phase's stated scope."""

from __future__ import annotations

from pathlib import Path

import pytest

from te.execution.halt import is_halted, set_halt
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base
from te.risk import killswitch
from te.risk.killswitch import KillSwitchTrippedError


@pytest.fixture(autouse=True)
def _reset_in_process_layer():  # noqa: ANN201
    """Every test starts with a cold in-process cache — the real module is
    a global, so tests must not leak state into each other."""
    killswitch.reset_in_process_cache()
    yield
    killswitch.reset_in_process_cache()


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'killswitch_test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_kill_switch_layers(session_factory) -> None:  # noqa: ANN001
    # Layer 1: an in-process trip blocks check() even though the DB has
    # never been touched.
    with session_factory() as session:
        assert is_halted(session) is False
        killswitch.trip(session, "manual in-process test trip")
        session.commit()

    assert killswitch.is_tripped_in_process() is True
    with session_factory() as session, pytest.raises(KillSwitchTrippedError, match="in-process"):
        killswitch.check(session)

    # Layer 2: simulate a process restart — the in-process cache goes cold,
    # but the DB flag (set by trip() above) still blocks check().
    killswitch.reset_in_process_cache()
    assert killswitch.is_tripped_in_process() is False

    with session_factory() as session, pytest.raises(KillSwitchTrippedError, match="DB flag"):
        killswitch.check(session)


def test_check_passes_when_neither_layer_is_tripped(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        killswitch.check(session)  # must not raise


def test_db_flag_set_directly_also_trips_check_after_cold_restart(session_factory) -> None:  # noqa: ANN001
    """Even if `trip()` was never called (e.g. `set_halt` was called by a
    different subsystem like `te.risk.limits`), a cold in-process cache
    still catches it via the DB flag."""
    with session_factory() as session:
        set_halt(session, "halted by a different subsystem")
        session.commit()

    assert killswitch.is_tripped_in_process() is False
    with session_factory() as session, pytest.raises(KillSwitchTrippedError, match="DB flag"):
        killswitch.check(session)


def test_throttle_sets_db_flag_but_does_not_trip_check(session_factory) -> None:  # noqa: ANN001
    """A throttle is weaker than a halt: `check()` must NOT raise on a
    throttle-only state — only `trip()` blocks `check()`."""
    with session_factory() as session:
        assert killswitch.is_currently_throttled(session) is False
        killswitch.throttle(session, "tier 0 slippage breach")
        session.commit()

    with session_factory() as session:
        assert killswitch.is_currently_throttled(session) is True
        assert killswitch.current_throttle_reason(session) == "tier 0 slippage breach"
        killswitch.check(session)  # must not raise — throttle never blocks submit()


def test_throttle_persists_across_a_simulated_restart(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        killswitch.throttle(session, "cusum throttle")
        session.commit()

    killswitch.reset_in_process_cache()
    with session_factory() as session:
        assert killswitch.is_currently_throttled(session) is True
