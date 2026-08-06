"""The one halt that clears itself, and every halt that must not.

A daily loss limit is scoped to one day by definition. Nothing unscoped it:
`set_halt` persists across restarts on purpose, and the only caller of
`clear_halt` was `POST /api/engine/reset-drawdown-breaker`. So the first
session to breach the limit blocked every entry from then on, until a human
noticed and clicked a button.

That is worse than it sounds for an unattended 1-2 month paper run. The
engine keeps running, the dashboard keeps rendering, the cycle keeps firing —
it just never enters anything again. The outage is indistinguishable from a
strategy that found no signals, so weeks of data can be lost with nothing
anywhere saying something is wrong.

The fix is narrow by design, and the narrowness is the part worth testing: a
drawdown breach, an overfill, a reconciliation mismatch and the kill switch
all still require a human, because none of them is a per-day condition.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from te.domain.money import Paise
from te.engine.scheduler import reset_daily_loss_halt
from te.execution.halt import (
    DAILY_LOSS_HALT,
    clear_daily_halt,
    halt_kind,
    halt_reason,
    is_halted,
    set_halt,
)
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, RiskEventRow
from te.persistence.repos.paper_trading import insert_trade
from te.risk.limits import LimitBreachError, RiskLimitsConfig, check_daily_loss_limit

_NOW = dt.datetime(2026, 8, 4, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'halt.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_a_daily_loss_halt_clears_at_the_next_session(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        set_halt(session, "lost too much today", kind=DAILY_LOSS_HALT)
        session.commit()

    assert reset_daily_loss_halt(session_factory) is True

    with session_factory() as session:
        assert is_halted(session) is False


def test_a_drawdown_halt_survives_the_next_session(session_factory) -> None:  # noqa: ANN001
    """The account is down 20% from its peak. A new day does not change that,
    and an engine that resumed trading on its own would be defeating the one
    breaker meant to stop a losing run."""
    with session_factory() as session:
        set_halt(session, "equity is 21% below peak", kind="max_drawdown")
        session.commit()

    assert reset_daily_loss_halt(session_factory) is False

    with session_factory() as session:
        assert is_halted(session) is True


def test_an_unlabelled_halt_survives(session_factory) -> None:  # noqa: ANN001
    """`kind` defaults to empty, so any caller that does not label its halt
    is treated as "needs a human". This also covers halts written to the DB
    before the `kind` key existed — they must not start clearing themselves
    the moment this code ships."""
    with session_factory() as session:
        set_halt(session, "something a human must look at")
        session.commit()

    assert reset_daily_loss_halt(session_factory) is False

    with session_factory() as session:
        assert is_halted(session) is True
        assert halt_kind(session) == ""


def test_resetting_when_nothing_is_halted_is_a_no_op(session_factory) -> None:  # noqa: ANN001
    """The common case — it runs every weekday morning and almost always has
    nothing to do."""
    assert reset_daily_loss_halt(session_factory) is False

    with session_factory() as session:
        assert is_halted(session) is False


def test_the_reset_is_recorded_rather_than_silent(session_factory) -> None:  # noqa: ANN001
    """An engine that un-halts itself overnight with no trace is not
    something anyone should have to discover by reading logs. The reason it
    cleared is kept too, so the record says WHAT was cleared."""
    with session_factory() as session:
        set_halt(session, "daily net -100500p breached the limit", kind=DAILY_LOSS_HALT)
        session.commit()

    reset_daily_loss_halt(session_factory)

    with session_factory() as session:
        events = session.query(RiskEventRow).all()
        assert [e.kind for e in events] == ["daily_loss_halt_reset"]
        assert "daily net -100500p" in events[0].detail


def test_the_real_daily_loss_limit_labels_its_halt_so_it_can_clear(session_factory) -> None:  # noqa: ANN001
    """End to end, against the real `check_daily_loss_limit` rather than a
    hand-set flag: the label has to survive the actual code path, or the
    reset job silently never fires in production while every unit test above
    still passes."""
    config = RiskLimitsConfig(
        max_daily_loss_paise=Paise(100_000),
        max_concurrent_positions=5,
        max_trades_per_day=20,
        max_drawdown_pct=Decimal(100),
    )
    with session_factory() as session:
        insert_trade(
            session,
            client_order_id="c-1",
            symbol="NIFTY04AUG2624600PE",
            exchange="NFO",
            strategy="orb",
            direction="long_put",
            lots=1,
            lot_size=65,
            entry_premium=Paise(7_400),
            exit_premium=Paise(5_900),
            gross_pnl=Paise(-150_000),
            costs=Paise(0),
            net_pnl=Paise(-150_000),
            exit_reason="stop",
            opened_at=_NOW - dt.timedelta(hours=1),
            closed_at=_NOW,
        )
        session.commit()

    with session_factory() as session:
        with pytest.raises(LimitBreachError):
            check_daily_loss_limit(session, config, on=_NOW.date(), now=_NOW)
        session.commit()  # set_halt() ran before the raise; persist it

    with session_factory() as session:
        assert is_halted(session) is True
        assert halt_kind(session) == DAILY_LOSS_HALT

    assert reset_daily_loss_halt(session_factory) is True


def test_clear_daily_halt_reports_whether_it_did_anything(session_factory) -> None:  # noqa: ANN001
    """The return value is what the scheduler job uses to decide whether to
    record a risk event, so a wrong answer means either a missing audit
    trail or a fabricated one."""
    with session_factory() as session:
        assert clear_daily_halt(session) is False

        set_halt(session, "daily", kind=DAILY_LOSS_HALT)
        assert clear_daily_halt(session) is True
        assert clear_daily_halt(session) is False, "already cleared — nothing more to do"
        assert halt_reason(session) == ""
