"""`run_session_audit` — the 15:35 scheduled job around the audit.

Two things matter here and neither is the arithmetic (that is
`test_session_audit.py`'s job):

1. A violation must be logged at ERROR. The audit's whole value is that a
   human finds out the same evening; a violation logged at INFO scrolls past.
2. The job must never raise. It runs on APScheduler's thread, and this project
   has already lost a whole session's bar recording to a background task that
   died quietly (see `WSRecorderSupervisor.is_running`). An audit that kills
   its own job is worse than no audit, because the silence looks like success.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from te.engine.scheduler import run_session_audit
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, OpenPositionRow, TradeRow
from te.settings import Settings


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'job.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        paper_cycle_max_trades_per_day=3,
        paper_cycle_max_consecutive_losses=3,
        paper_cycle_max_entries_per_underlying_per_day=2,
        paper_cycle_max_loss_per_trade_paise=70_000,
        paper_cycle_max_lots=1,
    )


def _losing_entry(session, *, coid: str, opened: dt.datetime, closed: dt.datetime) -> None:  # noqa: ANN001
    """One entry that loses well past the Rs 700 cap, so any day built from
    these trips several rules at once."""
    session.add(
        OpenPositionRow(
            client_order_id=coid,
            symbol="NIFTY11AUG2624550PE",
            exchange="NFO",
            strategy="orb",
            direction="long_put",
            lots=1,
            lot_size=65,
            entry_premium_paise=12_130,
            stop_paise=11_054,
            current_stop_paise=11_054,
            target_paise=24_000,
            max_hold_seconds=10_800,
            hard_exit_by="15:15:00",
            opened_at=opened,
            closed_at=closed,
        )
    )
    session.add(
        TradeRow(
            client_order_id=coid,
            symbol="NIFTY11AUG2624550PE",
            exchange="NFO",
            strategy="orb",
            direction="long_put",
            lots=1,
            lot_size=65,
            entry_premium_paise=12_130,
            exit_premium_paise=10_900,
            gross_pnl_paise=-79_950,
            costs_paise=6_436,
            net_pnl_paise=-86_386,
            exit_reason="stop",
            mode="paper",
            opened_at=opened,
            closed_at=closed,
            stop_paise=11_054,
        )
    )


def test_a_breaking_day_is_reported_at_error(session_factory, settings) -> None:  # noqa: ANN001
    """Four entries against a cap of three, all losing past the per-trade cap.

    Asserted at ERROR specifically: a broken risk limit is the engine having
    done something it promised not to do, not a note for later.

    Captured through `structlog.testing`, not pytest's `caplog`. `caplog` reads
    empty here — this project renders through structlog's own pipeline, so the
    stdlib handler pytest attaches never sees the event. A first version of
    this test asserted on `caplog.text` and would have passed happily against a
    job that logged nothing at all.
    """
    today = dt.datetime.now(dt.UTC).replace(hour=5, minute=0, second=0, microsecond=0)
    with session_factory() as session:
        for i in range(4):
            _losing_entry(
                session,
                coid=f"e-{i}",
                opened=today + dt.timedelta(minutes=10 * i),
                closed=today + dt.timedelta(minutes=10 * i + 5),
            )
        session.commit()

    with capture_logs() as logs:
        audit = run_session_audit(session_factory, settings)

    assert audit is not None
    assert not audit.clean
    assert "max_trades_per_day" in [f.rule for f in audit.violations]

    headline = [e for e in logs if "SESSION AUDIT FAILED" in str(e.get("event", ""))]
    assert headline, f"no headline failure was logged; got {[e.get('event') for e in logs]}"
    assert headline[0]["log_level"] == "error"
    assert "max_trades_per_day" in headline[0]["violations"]

    # Each violation also logged individually, so the detail is not buried in
    # a single summary line an operator has to go decode.
    per_finding = [e for e in logs if e.get("rule") == "max_trades_per_day" and e.get("log_level") == "error"]
    assert per_finding, "the breach was summarised but never logged with its own expected/actual"


def test_a_quiet_day_is_clean_and_does_not_shout(session_factory, settings) -> None:  # noqa: ANN001
    """No entries at all — a holiday, or a day nothing fired. Must not report
    a violation, or the daily report becomes noise a human stops reading."""
    with capture_logs() as logs:
        audit = run_session_audit(session_factory, settings)

    assert audit is not None
    assert audit.clean
    assert audit.entries == []
    assert not [e for e in logs if e.get("log_level") == "error"]


def test_the_job_survives_a_broken_database(tmp_path: Path, settings) -> None:  # noqa: ANN001
    """The tables do not exist. The job must return `None`, not raise —
    AND the failure must be visible, not silently swallowed.

    A raise here propagates into APScheduler's executor, and this project has
    already lost a full session of bar recording to exactly that shape of
    quiet background death. But `return None` alone is not enough: a mutation
    audit found that replacing the `except` block's `logger.exception(...)`
    with a bare `pass` leaves this test just as green — asserting only the
    return value cannot distinguish "failed loudly, then returned None" from
    "failed silently, then returned None". A bad migration or a renamed
    column would then produce a day with NO audit and NO error in the logs,
    which is indistinguishable from a clean day — see the module docstring's
    "the silence looks like success"."""
    engine = make_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    factory = make_session_factory(engine)  # deliberately no create_all

    with capture_logs() as logs:
        result = run_session_audit(factory, settings)

    assert result is None
    errors = [e for e in logs if e.get("log_level") == "error"]
    assert errors, f"no error-level event was logged for the broken database; got {[e.get('event') for e in logs]}"
