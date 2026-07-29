"""`te.risk.limits` — daily loss limit, max concurrent positions, max
trades/day, all persisted (not in-memory-only) — proven here by re-opening
a fresh session factory against the SAME db FILE (never `:memory:`) and
confirming the halt is still in effect, simulating a process restart."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.domain.money import Paise
from te.execution.halt import is_halted
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base
from te.persistence.repos.paper_trading import insert_trade
from te.risk.limits import (
    LimitBreachError,
    RiskLimitsConfig,
    check_daily_loss_limit,
    check_max_concurrent_positions,
    check_max_trades_per_day,
)

ON = dt.date(2026, 7, 29)
NOW = dt.datetime(2026, 7, 29, 14, 0, tzinfo=dt.UTC)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "risk_limits_test.db"


def _session_factory(db_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _config(**overrides: object) -> RiskLimitsConfig:
    defaults: dict[str, object] = {
        "max_daily_loss_paise": Paise(5_000_00), "max_concurrent_positions": 2, "max_trades_per_day": 5
    }
    defaults.update(overrides)
    return RiskLimitsConfig(**defaults)  # type: ignore[arg-type]


def _insert_losing_trade(session, net_pnl_paise: int) -> None:  # noqa: ANN001
    insert_trade(
        session,
        client_order_id="c-1",
        symbol="NIFTY30JUN2626500CE",
        exchange="NFO",
        strategy="orb",
        direction="long_call",
        lots=1,
        lot_size=65,
        entry_premium=Paise(3_000),
        exit_premium=Paise(1_000),
        gross_pnl=Paise(net_pnl_paise),
        costs=Paise(0),
        net_pnl=Paise(net_pnl_paise),
        exit_reason="stop",
        opened_at=NOW - dt.timedelta(hours=1),
        closed_at=NOW,
    )


def test_daily_loss_limit_halts_and_persists(db_path: Path) -> None:
    factory = _session_factory(db_path)
    config = _config(max_daily_loss_paise=Paise(4_000_00))

    with factory() as session:
        _insert_losing_trade(session, -5_000_00)
        session.commit()

    with factory() as session:
        with pytest.raises(LimitBreachError):
            check_daily_loss_limit(session, config, on=ON, now=NOW)
        session.commit()  # set_halt() ran before the raise; persist it

    with factory() as session:
        assert is_halted(session) is True

    # Simulate a process restart: a brand-new engine/session factory against
    # the SAME db file (never :memory:).
    restarted_factory = _session_factory(db_path)
    with restarted_factory() as session:
        assert is_halted(session) is True


def test_daily_loss_limit_does_not_trip_when_within_budget(db_path: Path) -> None:
    factory = _session_factory(db_path)
    config = _config(max_daily_loss_paise=Paise(10_000_00))

    with factory() as session:
        _insert_losing_trade(session, -2_000_00)
        session.commit()

    with factory() as session:
        check_daily_loss_limit(session, config, on=ON, now=NOW)  # must not raise
        session.commit()

    with factory() as session:
        assert is_halted(session) is False


def test_max_concurrent_positions_blocks_at_limit(db_path: Path) -> None:
    from te.domain.signal import ExitPlan
    from te.persistence.repos.paper_trading import insert_open_position

    factory = _session_factory(db_path)
    config = _config(max_concurrent_positions=1)
    plan = ExitPlan(
        stop=Paise(1_800),
        trailing_distance=Paise(200),
        target=Paise(3_000),
        max_hold=dt.timedelta(hours=3),
        hard_exit_by=dt.time(15, 20),
    )

    with factory() as session:
        insert_open_position(
            session,
            client_order_id="c-1",
            symbol="NIFTY30JUN2626500CE",
            exchange="NFO",
            strategy="orb",
            direction="long_call",
            lots=1,
            lot_size=65,
            entry_premium=Paise(2_000),
            exit_plan=plan,
            opened_at=NOW,
        )
        session.commit()

    with factory() as session, pytest.raises(LimitBreachError):
        check_max_concurrent_positions(session, config)


def test_max_trades_per_day_blocks_at_limit(db_path: Path) -> None:
    factory = _session_factory(db_path)
    config = _config(max_trades_per_day=1)

    with factory() as session:
        _insert_losing_trade(session, 500_00)
        session.commit()

    with factory() as session, pytest.raises(LimitBreachError):
        check_max_trades_per_day(session, config, on=ON)
