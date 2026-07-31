"""`te.risk.limits` — daily loss limit, max concurrent positions, max
trades/day, all persisted (not in-memory-only) — proven here by re-opening
a fresh session factory against the SAME db FILE (never `:memory:`) and
confirming the halt is still in effect, simulating a process restart."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
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
    check_max_drawdown,
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
        trailing_activation=Paise(2_200),
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


def test_daily_loss_limit_counts_unrealized_pnl_on_open_positions(db_path: Path) -> None:
    """Regression: an unattended session could previously run unlimited
    unrealized drawdown across open positions without ever tripping the
    daily-loss halt — only CLOSED trades counted."""
    factory = _session_factory(db_path)
    config = _config(max_daily_loss_paise=Paise(4_000_00))

    with factory() as session:
        # No closed trades at all — realized P&L is exactly 0 — but a huge
        # unrealized loss must still trip the halt.
        with pytest.raises(LimitBreachError):
            check_daily_loss_limit(session, config, on=ON, now=NOW, unrealized_pnl_paise=Paise(-5_000_00))
        session.commit()

    with factory() as session:
        assert is_halted(session) is True


def test_daily_loss_limit_stays_clear_with_a_small_unrealized_loss(db_path: Path) -> None:
    factory = _session_factory(db_path)
    config = _config(max_daily_loss_paise=Paise(10_000_00))

    with factory() as session:
        check_daily_loss_limit(session, config, on=ON, now=NOW, unrealized_pnl_paise=Paise(-1_000_00))  # must not raise
        session.commit()

    with factory() as session:
        assert is_halted(session) is False


def test_max_drawdown_halts_when_equity_drops_from_its_peak(db_path: Path) -> None:
    factory = _session_factory(db_path)
    config = _config(max_drawdown_pct=Decimal(10))

    with factory() as session:
        # First observation establishes the peak (10,000).
        check_max_drawdown(session, config, now=NOW, current_equity_paise=Paise(10_000_00))
        session.commit()

    with factory() as session:
        # Equity has since dropped 15% from that peak — breaches the 10% cap.
        with pytest.raises(LimitBreachError):
            check_max_drawdown(session, config, now=NOW, current_equity_paise=Paise(8_500_00))
        session.commit()

    with factory() as session:
        assert is_halted(session) is True


def test_max_drawdown_peak_watermark_survives_a_restart_and_never_ratchets_down(db_path: Path) -> None:
    factory = _session_factory(db_path)
    config = _config(max_drawdown_pct=Decimal(50))

    with factory() as session:
        check_max_drawdown(session, config, now=NOW, current_equity_paise=Paise(10_000_00))
        session.commit()
    with factory() as session:
        # Equity dips but stays within the 50% band — must not raise, and
        # must NOT lower the stored peak.
        check_max_drawdown(session, config, now=NOW, current_equity_paise=Paise(9_000_00))
        session.commit()

    # Simulate a restart: fresh session factory against the same db file.
    restarted_factory = _session_factory(db_path)
    with restarted_factory() as session:
        # A drop to just above 50% of the ORIGINAL 10,000 peak (not the
        # lower 9,000 the account dipped to) must still be safe.
        check_max_drawdown(session, config, now=NOW, current_equity_paise=Paise(5_100_00))  # must not raise
        session.commit()
    with restarted_factory() as session:
        # But a drop below 50% of the original peak trips it.
        with pytest.raises(LimitBreachError):
            check_max_drawdown(session, config, now=NOW, current_equity_paise=Paise(4_900_00))


def test_max_drawdown_disabled_by_default_matches_pre_existing_config(db_path: Path) -> None:
    """`max_drawdown_pct` defaults to 100 (a no-op) so a `RiskLimitsConfig`
    built before this guardrail existed keeps behaving identically."""
    factory = _session_factory(db_path)
    config = _config()  # no max_drawdown_pct override -> Decimal(100)

    with factory() as session:
        check_max_drawdown(session, config, now=NOW, current_equity_paise=Paise(10_000_00))
        session.commit()
    with factory() as session:
        # Equity crashes to nearly 0 — still must not raise at the default 100%.
        check_max_drawdown(session, config, now=NOW, current_equity_paise=Paise(1))
