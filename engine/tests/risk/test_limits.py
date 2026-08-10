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
from te.persistence.models import Base, OpenPositionRow
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
        entry_premium=Paise(2_000),
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


def test_max_concurrent_positions_allows_one_below_the_limit(db_path: Path) -> None:
    """`count == limit - 1` must NOT raise.

    The existing at-limit test sat AT the limit with cap=1/count=1, so
    `>=` could be weakened to `>= limit - 1` (refusing entries a whole
    position early on a compliant day) and stay green.
    """
    from te.domain.signal import ExitPlan
    from te.persistence.repos.paper_trading import insert_open_position

    factory = _session_factory(db_path)
    config = _config(max_concurrent_positions=2)
    plan = ExitPlan(
        entry_premium=Paise(2_000),
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

    with factory() as session:
        check_max_concurrent_positions(session, config)  # must not raise


def test_max_trades_per_day_blocks_at_limit(db_path: Path) -> None:
    factory = _session_factory(db_path)
    config = _config(max_trades_per_day=1)

    with factory() as session:
        # The ENTRY ledger row as well as the closed trade. Every real closed
        # trade has one — `open_positions` rows are written at open and never
        # deleted — and this fixture used to omit it, which is why the cap
        # could count the exit ledger alone and still look correct here.
        _open_position(session, coid="coid-closed", on=ON, closed=True)
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


def _open_position(session, *, coid: str, on: dt.date, closed: bool = False) -> None:  # noqa: ANN001
    """A row in the ENTRY ledger. With `closed=False` it is a LIVE position:
    no `closed_at`, and no `TradeRow` anywhere. That is the state the trade
    counter was blind to."""
    session.add(
        OpenPositionRow(
            client_order_id=coid,
            symbol="NIFTY11AUG2624550CE",
            exchange="NFO",
            strategy="orb",
            direction="long",
            lots=1,
            lot_size=65,
            entry_premium_paise=16_680,
            stop_paise=15_000,
            current_stop_paise=15_000,
            target_paise=30_000,
            max_hold_seconds=10_800,
            hard_exit_by="15:15",
            opened_at=dt.datetime.combine(on, dt.time(5, 0), tzinfo=dt.UTC),
            closed_at=dt.datetime.combine(on, dt.time(6, 0), tzinfo=dt.UTC) if closed else None,
        )
    )


def test_max_trades_per_day_counts_positions_that_are_still_open(db_path: Path) -> None:
    """The 2026-08-07 breach, as a test.

    Six trades ran against a cap of three and this guard never fired. It
    counted `trades`, the EXIT ledger, so at each entry decision the earlier
    positions — still open, no `TradeRow` yet — counted as zero. Every
    existing test for this cap inserted a CLOSED trade, which is precisely
    the case the broken code handled correctly.

    No closed trade is created here at all: if the guard reads the exit
    ledger, it sees an empty day and permits trade number four.
    """
    factory = _session_factory(db_path)
    with factory() as session:
        for i in range(3):
            _open_position(session, coid=f"open-{i}", on=ON)
        session.commit()

    with factory() as session, pytest.raises(LimitBreachError) as exc:
        check_max_trades_per_day(session, _config(max_trades_per_day=3), on=ON)

    assert exc.value.kind == "max_trades"
    assert "3 trade(s) already placed today" in exc.value.reason


def test_max_trades_per_day_counts_open_and_closed_together(db_path: Path) -> None:
    """One still running plus one already closed is TWO trades placed.

    The entry ledger keeps the row after close, so counting it alone is both
    complete and free of the double-counting that adding the two ledgers
    together would risk.
    """
    factory = _session_factory(db_path)
    with factory() as session:
        _open_position(session, coid="still-open", on=ON)
        _open_position(session, coid="already-closed", on=ON, closed=True)
        _insert_losing_trade(session, 500_00)
        session.commit()

    with factory() as session, pytest.raises(LimitBreachError):
        check_max_trades_per_day(session, _config(max_trades_per_day=2), on=ON)


def test_max_trades_per_day_allows_the_last_permitted_trade(db_path: Path) -> None:
    """`count == limit - 1` must NOT raise.

    Every existing cap test sat AT the limit, so `>=` could be weakened to
    `>= limit - 1` and stay green — refusing the last permitted trade of
    every day and reporting a breach on a compliant session.
    """
    factory = _session_factory(db_path)
    with factory() as session:
        for i in range(2):
            _open_position(session, coid=f"open-{i}", on=ON)
        session.commit()

    with factory() as session:
        check_max_trades_per_day(session, _config(max_trades_per_day=3), on=ON)  # must not raise


# --- IST day-boundary edges (`_day_bounds`) -------------------------------
#
# Every fixture above (and in every other test file in scope) seeds its rows
# at a comfortable mid-day UTC time, which lands inside both the buggy
# UTC-stamped window and the correct IST one — so nothing pins either edge
# of the IST trading day `_day_bounds` computes. `on`'s IST day is
# `[on-1 18:30 UTC, on 18:30 UTC)`; these pin both ends against the
# ENTRY-ledger counter (`entries_count_today`, via `check_max_trades_per_day`)
# and the EXIT-ledger/realized-P&L counter (`daily_net_pnl_paise`, via
# `check_daily_loss_limit`).


def test_entries_count_today_counts_a_row_just_after_ist_midnight(db_path: Path) -> None:
    """00:05 IST on `ON` == 18:35 UTC on `ON - 1 day` — must count for `ON`,
    not for the previous UTC calendar date."""
    factory = _session_factory(db_path)
    just_after_ist_midnight = dt.datetime.combine(ON - dt.timedelta(days=1), dt.time(18, 35), tzinfo=dt.UTC)

    with factory() as session:
        _open_position_at(session, coid="ist-midnight-edge", opened_at=just_after_ist_midnight)
        session.commit()

    with factory() as session, pytest.raises(LimitBreachError):
        check_max_trades_per_day(session, _config(max_trades_per_day=1), on=ON)


def test_entries_count_today_counts_a_row_at_ist_market_open(db_path: Path) -> None:
    """09:15 IST on `ON` (market open) == 03:45 UTC on `ON` — must count for
    `ON`. The old UTC-stamped `[on 00:00, on 23:59:59]` window happened to
    get this one right too (03:45 UTC sits inside it), but the fixed window
    must keep getting it right."""
    factory = _session_factory(db_path)
    market_open = dt.datetime.combine(ON, dt.time(3, 45), tzinfo=dt.UTC)

    with factory() as session:
        _open_position_at(session, coid="market-open-edge", opened_at=market_open)
        session.commit()

    with factory() as session, pytest.raises(LimitBreachError):
        check_max_trades_per_day(session, _config(max_trades_per_day=1), on=ON)


def test_daily_net_pnl_counts_a_trade_closed_just_after_ist_midnight(db_path: Path) -> None:
    """A trade CLOSED at 00:05 IST on `ON` (18:35 UTC on `ON - 1`) must
    breach `ON`'s daily loss limit — the same IST-midnight edge, now against
    the realized-P&L counter (`daily_net_pnl_paise`, feeding
    `check_daily_loss_limit`) rather than the entry counter."""
    factory = _session_factory(db_path)
    config = _config(max_daily_loss_paise=Paise(4_000_00))
    just_after_ist_midnight = dt.datetime.combine(ON - dt.timedelta(days=1), dt.time(18, 35), tzinfo=dt.UTC)

    with factory() as session:
        insert_trade(
            session,
            client_order_id="c-ist-midnight",
            symbol="NIFTY30JUN2626500CE",
            exchange="NFO",
            strategy="orb",
            direction="long_call",
            lots=1,
            lot_size=65,
            entry_premium=Paise(3_000),
            exit_premium=Paise(1_000),
            gross_pnl=Paise(-5_000_00),
            costs=Paise(0),
            net_pnl=Paise(-5_000_00),
            exit_reason="stop",
            opened_at=just_after_ist_midnight - dt.timedelta(hours=1),
            closed_at=just_after_ist_midnight,
        )
        session.commit()

    with factory() as session, pytest.raises(LimitBreachError):
        check_daily_loss_limit(session, config, on=ON, now=NOW)


def test_daily_net_pnl_counts_a_trade_closed_at_ist_market_open(db_path: Path) -> None:
    """A trade CLOSED at 09:15 IST on `ON` (03:45 UTC on `ON`) must breach
    `ON`'s daily loss limit."""
    factory = _session_factory(db_path)
    config = _config(max_daily_loss_paise=Paise(4_000_00))
    market_open = dt.datetime.combine(ON, dt.time(3, 45), tzinfo=dt.UTC)

    with factory() as session:
        insert_trade(
            session,
            client_order_id="c-market-open",
            symbol="NIFTY30JUN2626500CE",
            exchange="NFO",
            strategy="orb",
            direction="long_call",
            lots=1,
            lot_size=65,
            entry_premium=Paise(3_000),
            exit_premium=Paise(1_000),
            gross_pnl=Paise(-5_000_00),
            costs=Paise(0),
            net_pnl=Paise(-5_000_00),
            exit_reason="stop",
            opened_at=market_open - dt.timedelta(hours=1),
            closed_at=market_open,
        )
        session.commit()

    with factory() as session, pytest.raises(LimitBreachError):
        check_daily_loss_limit(session, config, on=ON, now=NOW)


def _open_position_at(session, *, coid: str, opened_at: dt.datetime) -> None:  # noqa: ANN001
    """Like `_open_position` above, but takes an exact `opened_at` instant
    rather than deriving one from a calendar date — needed to pin the IST
    day-boundary edges precisely."""
    session.add(
        OpenPositionRow(
            client_order_id=coid,
            symbol="NIFTY11AUG2624550CE",
            exchange="NFO",
            strategy="orb",
            direction="long",
            lots=1,
            lot_size=65,
            entry_premium_paise=16_680,
            stop_paise=15_000,
            current_stop_paise=15_000,
            target_paise=30_000,
            max_hold_seconds=10_800,
            hard_exit_by="15:15",
            opened_at=opened_at,
            closed_at=None,
        )
    )
