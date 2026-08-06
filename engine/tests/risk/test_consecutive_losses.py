"""`check_consecutive_losses` — the behavioural stand-down after N straight
stop-outs (Phase 0 item 3 of `docs/FableImprovements.md`).

Its distinguishing property versus the other two loss guards is that it does
NOT halt: a streak is evidence about today's conditions, not a safety
failure, so it expires with the trading day rather than persisting a flag a
human has to clear.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from te.domain.money import Paise
from te.execution.halt import is_halted
from te.persistence.db import make_engine, make_session_factory, session_scope
from te.persistence.models import Base, TradeRow
from te.risk.limits import LimitBreachError, RiskLimitsConfig, check_consecutive_losses

ON = dt.date(2026, 7, 29)


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'streak.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _config(max_consecutive_losses: int) -> RiskLimitsConfig:
    return RiskLimitsConfig(
        max_daily_loss_paise=Paise(10_000_000),
        max_concurrent_positions=5,
        max_trades_per_day=50,
        max_consecutive_losses=max_consecutive_losses,
    )


def _trade(session: Session, *, minute: int, net_paise: int) -> None:
    closed = dt.datetime.combine(ON, dt.time(10, 0), tzinfo=dt.UTC) + dt.timedelta(minutes=minute)
    session.add(
        TradeRow(
            client_order_id=f"coid-{minute}",
            symbol="NIFTY30JUL2624500CE",
            exchange="NFO",
            strategy="orb",
            direction="long_call",
            lots=1,
            lot_size=65,
            entry_premium_paise=10_000,
            exit_premium_paise=10_000 + net_paise,
            gross_pnl_paise=net_paise,
            costs_paise=0,
            net_pnl_paise=net_paise,
            exit_reason="stop" if net_paise < 0 else "target",
            opened_at=closed - dt.timedelta(minutes=5),
            closed_at=closed,
        )
    )


def test_three_straight_losses_blocks_further_entries(session_factory) -> None:  # noqa: ANN001
    with session_scope(session_factory) as session:
        for minute, net in ((1, -500), (2, -500), (3, -500)):
            _trade(session, minute=minute, net_paise=net)

    with session_factory() as session, pytest.raises(LimitBreachError) as exc:
        check_consecutive_losses(session, _config(3), on=ON)

    assert exc.value.kind == "consecutive_losses"
    assert "3 consecutive losing trade(s)" in exc.value.reason


def test_two_straight_losses_is_still_allowed(session_factory) -> None:  # noqa: ANN001
    with session_scope(session_factory) as session:
        for minute, net in ((1, -500), (2, -500)):
            _trade(session, minute=minute, net_paise=net)

    with session_factory() as session:
        check_consecutive_losses(session, _config(3), on=ON)  # must not raise


def test_a_single_winner_resets_the_streak(session_factory) -> None:
    """The whole point of a CONSECUTIVE-loss rule: three losses followed by a
    win is not a streak. Counting total losses instead would keep the engine
    stood down for the rest of a day it had already recovered from."""
    with session_scope(session_factory) as session:
        for minute, net in ((1, -500), (2, -500), (3, -500), (4, +900)):
            _trade(session, minute=minute, net_paise=net)

    with session_factory() as session:
        check_consecutive_losses(session, _config(3), on=ON)  # must not raise


def test_breakeven_counts_as_a_reset_not_a_loss(session_factory) -> None:  # noqa: ANN001
    with session_scope(session_factory) as session:
        for minute, net in ((1, -500), (2, -500), (3, 0), (4, -500)):
            _trade(session, minute=minute, net_paise=net)

    with session_factory() as session:
        check_consecutive_losses(session, _config(3), on=ON)  # only 1 trailing loss


def test_it_stands_down_without_halting(session_factory) -> None:
    """Unlike `check_daily_loss_limit`/`check_max_drawdown`, this must NOT
    set the persisted halt flag — that would survive the restart and the day,
    turning a self-clearing behavioural pause into something a human has to
    come and clear."""
    with session_scope(session_factory) as session:
        for minute in (1, 2, 3):
            _trade(session, minute=minute, net_paise=-500)

    with session_factory() as session:
        with pytest.raises(LimitBreachError):
            check_consecutive_losses(session, _config(3), on=ON)
        assert is_halted(session) is False


def test_zero_disables_the_check(session_factory) -> None:  # noqa: ANN001
    with session_scope(session_factory) as session:
        for minute in range(1, 11):
            _trade(session, minute=minute, net_paise=-500)

    with session_factory() as session:
        check_consecutive_losses(session, _config(0), on=ON)  # must not raise


def test_yesterdays_losses_do_not_carry_into_today(session_factory) -> None:
    """The stand-down expires with the session. Reading `trades_today` is
    what makes that true, so it is pinned here."""
    with session_scope(session_factory) as session:
        for minute in (1, 2, 3):
            _trade(session, minute=minute, net_paise=-500)

    with session_factory() as session:
        check_consecutive_losses(session, _config(3), on=ON + dt.timedelta(days=1))  # must not raise
