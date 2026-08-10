"""`te.persistence.repos.paper_trading` — the IST-trading-day half-open
window `_day_bounds` computes (`[D-1 18:30Z, D 18:30Z)`), swept across every
query function that uses it, plus the non-window equality filters
(`conditions_for`'s `evaluation_id`, `daily_pnl`'s `month`).

Every existing boundary test elsewhere in this codebase (`tests/risk/
test_limits.py`, `tests/risk/test_consecutive_losses.py`) seeds a row 5
minutes off either edge (00:05 IST / 09:15 IST market open) — enough to prove
the window is IST-aware, but NOT enough to distinguish `>=` from `>`, or `<`
from `<=`, at the exact instant. These tests pin the exact boundary instant
itself: a row at `start` must count, a row at `end` must not."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.domain.money import Paise
from te.domain.signal import ExitPlan
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import (
    CycleEvaluationRow,
    EvaluationConditionRow,
    OpenPositionRow,
    OrderEventRow,
    TradeRow,
)
from te.persistence.repos.paper_trading import (
    conditions_for,
    daily_net_pnl_paise,
    daily_pnl,
    entries_count_today,
    equity_curve,
    evaluations_today,
    insert_open_position,
    insert_trade,
    order_ids_today,
    total_net_pnl_paise,
    trades_closed_since,
    trades_count_today,
    trades_today,
    underlying_entries_today,
)

ON = dt.date(2026, 7, 29)
START = dt.datetime(2026, 7, 28, 18, 30, tzinfo=dt.UTC)  # 00:00 IST on ON — inclusive
END = dt.datetime(2026, 7, 29, 18, 30, tzinfo=dt.UTC)  # 24:00 IST on ON (00:00 IST on ON+1) — exclusive


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    from te.persistence.models import Base

    engine = make_engine(f"sqlite:///{tmp_path / 'paper_trading_test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _trade(*, coid: str, closed_at: dt.datetime, net_paise: int = -100_00, symbol: str = "NIFTY30JUN2626500CE") -> TradeRow:
    return TradeRow(
        client_order_id=coid,
        symbol=symbol,
        exchange="NFO",
        strategy="orb",
        direction="long_call",
        lots=1,
        lot_size=65,
        entry_premium_paise=3_000,
        exit_premium_paise=3_000 + net_paise,
        gross_pnl_paise=net_paise,
        costs_paise=0,
        net_pnl_paise=net_paise,
        exit_reason="stop" if net_paise < 0 else "target",
        opened_at=closed_at - dt.timedelta(hours=1),
        closed_at=closed_at,
    )


def _open_position(*, coid: str, opened_at: dt.datetime, closed_at: dt.datetime | None = None,
                    symbol: str = "NIFTY11AUG2624550CE", strategy: str = "orb") -> OpenPositionRow:
    return OpenPositionRow(
        client_order_id=coid,
        symbol=symbol,
        exchange="NFO",
        strategy=strategy,
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
        closed_at=closed_at,
    )


# --- evaluations_today (`CycleEvaluationRow.ts`, line 109) -----------------


def test_evaluations_today_includes_a_row_exactly_at_start(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(
            CycleEvaluationRow(
                cycle_id=1, evaluation_id="eval-at-start", ts=START,
                strategy="orb", instrument="NIFTY", verdict="skipped", reason="r",
            )
        )
        session.commit()

    with session_factory() as session:
        rows = evaluations_today(session, ON)

    assert [r.evaluation_id for r in rows] == ["eval-at-start"]


def test_evaluations_today_excludes_a_row_exactly_at_end(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(
            CycleEvaluationRow(
                cycle_id=1, evaluation_id="eval-at-end", ts=END,
                strategy="orb", instrument="NIFTY", verdict="skipped", reason="r",
            )
        )
        session.commit()

    with session_factory() as session:
        rows = evaluations_today(session, ON)

    assert rows == []


# --- conditions_for (`EvaluationConditionRow.evaluation_id`, line 121) -----


def test_conditions_for_only_returns_rows_for_the_matching_evaluation_id(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(
            EvaluationConditionRow(
                evaluation_id="eval-a", seq=0, label="orb-breakout", required="true",
                actual="true", passed=True, evaluated=True,
            )
        )
        session.add(
            EvaluationConditionRow(
                evaluation_id="eval-b", seq=0, label="different-eval", required="true",
                actual="true", passed=True, evaluated=True,
            )
        )
        session.commit()

    with session_factory() as session:
        rows = conditions_for(session, "eval-a")

    assert [r.label for r in rows] == ["orb-breakout"]


# --- underlying_entries_today (`TradeRow.closed_at`, lines 360-361) --------


def test_underlying_entries_today_closed_count_includes_a_trade_closed_exactly_at_start(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(_trade(coid="c-1", closed_at=START, symbol="NIFTY30JUN2626500CE"))
        session.commit()

    with session_factory() as session:
        count = underlying_entries_today(session, strategy="orb", underlying="NIFTY", on=ON)

    assert count == 1


def test_underlying_entries_today_closed_count_excludes_a_trade_closed_exactly_at_end(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(_trade(coid="c-1", closed_at=END, symbol="NIFTY30JUN2626500CE"))
        session.commit()

    with session_factory() as session:
        count = underlying_entries_today(session, strategy="orb", underlying="NIFTY", on=ON)

    assert count == 0


# --- trades_today (`TradeRow.closed_at`, line 370) -------------------------


def test_trades_today_includes_a_trade_closed_exactly_at_start(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(_trade(coid="c-1", closed_at=START))
        session.commit()

    with session_factory() as session:
        rows = trades_today(session, ON)

    assert [r.client_order_id for r in rows] == ["c-1"]


def test_trades_today_excludes_a_trade_closed_exactly_at_end(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(_trade(coid="c-1", closed_at=END))
        session.commit()

    with session_factory() as session:
        rows = trades_today(session, ON)

    assert rows == []


# --- trades_count_today (`TradeRow.closed_at`, line 403) -------------------


def test_trades_count_today_counts_a_trade_closed_exactly_at_start(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(_trade(coid="c-1", closed_at=START))
        session.commit()

    with session_factory() as session:
        assert trades_count_today(session, ON) == 1


def test_trades_count_today_excludes_a_trade_closed_exactly_at_end(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(_trade(coid="c-1", closed_at=END))
        session.commit()

    with session_factory() as session:
        assert trades_count_today(session, ON) == 0


# --- entries_count_today (`OpenPositionRow.opened_at`, line 432) -----------


def test_entries_count_today_counts_a_position_opened_exactly_at_start(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(_open_position(coid="o-1", opened_at=START))
        session.commit()

    with session_factory() as session:
        assert entries_count_today(session, ON) == 1


def test_entries_count_today_excludes_a_position_opened_exactly_at_end(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(_open_position(coid="o-1", opened_at=END))
        session.commit()

    with session_factory() as session:
        assert entries_count_today(session, ON) == 0


# --- daily_net_pnl_paise (`TradeRow.closed_at`, line 442) -------------------


def test_daily_net_pnl_paise_counts_a_trade_closed_exactly_at_start(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(_trade(coid="c-1", closed_at=START, net_paise=-1_234))
        session.commit()

    with session_factory() as session:
        assert daily_net_pnl_paise(session, ON) == Paise(-1_234)


def test_daily_net_pnl_paise_excludes_a_trade_closed_exactly_at_end(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(_trade(coid="c-1", closed_at=END, net_paise=-1_234))
        session.commit()

    with session_factory() as session:
        assert daily_net_pnl_paise(session, ON) == Paise(0)


# --- total_net_pnl_paise (`TradeRow.closed_at >= since`, line 468) ---------


def test_total_net_pnl_paise_includes_a_trade_closed_exactly_at_since(session_factory) -> None:  # noqa: ANN001
    """The docstring's contract: trades that closed BEFORE `since` are
    excluded — a trade closed AT `since` itself belongs to the new period and
    must count."""
    since = dt.datetime(2026, 7, 29, 10, 0, tzinfo=dt.UTC)
    with session_factory() as session:
        session.add(_trade(coid="c-1", closed_at=since, net_paise=750_00))
        session.commit()

    with session_factory() as session:
        assert total_net_pnl_paise(session, since=since) == Paise(750_00)


# --- order_ids_today (`OrderEventRow.ts`, line 504) -------------------------


def test_order_ids_today_includes_an_order_event_exactly_at_start(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(OrderEventRow(client_order_id="co-1", seq=0, event_type="placed", payload_json="{}", ts=START))
        session.commit()

    with session_factory() as session:
        assert order_ids_today(session, ON) == ["co-1"]


def test_order_ids_today_excludes_an_order_event_exactly_at_end(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(OrderEventRow(client_order_id="co-1", seq=0, event_type="placed", payload_json="{}", ts=END))
        session.commit()

    with session_factory() as session:
        assert order_ids_today(session, ON) == []


# --- daily_pnl month filter (`func.strftime(...) == month`, line 524) ------


def test_daily_pnl_month_filter_excludes_a_different_months_trade(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(_trade(coid="c-jul", closed_at=dt.datetime(2026, 7, 15, 10, 0, tzinfo=dt.UTC), net_paise=100_00))
        session.add(_trade(coid="c-aug", closed_at=dt.datetime(2026, 8, 15, 10, 0, tzinfo=dt.UTC), net_paise=200_00))
        session.commit()

    with session_factory() as session:
        rows = daily_pnl(session, month="2026-07")

    assert [d.isoformat() for d, _pnl, _count in rows] == ["2026-07-15"]


# --- trades_closed_since (`TradeRow.closed_at >= start`, line 564) ---------


def test_trades_closed_since_includes_a_trade_closed_exactly_at_the_cutoff_instant(session_factory) -> None:  # noqa: ANN001
    """`cutoff` is a DATE (inclusive) combined at raw UTC midnight (this
    function's own convention, distinct from `_day_bounds`'s IST window — see
    `recent_session_dates`, which groups by the same UTC calendar date via
    `func.date`) — a trade closed at exactly that UTC midnight instant must
    count as "on or after cutoff"."""
    cutoff = dt.date(2026, 7, 29)
    exactly_at_cutoff = dt.datetime(2026, 7, 29, 0, 0, tzinfo=dt.UTC)
    with session_factory() as session:
        session.add(_trade(coid="c-1", closed_at=exactly_at_cutoff))
        session.commit()

    with session_factory() as session:
        rows = trades_closed_since(session, cutoff)

    assert [r.client_order_id for r in rows] == ["c-1"]


# --- equity_curve from_/to (lines 553) --------------------------------------


def test_equity_curve_window_includes_both_endpoints_and_nothing_outside_it(session_factory) -> None:  # noqa: ANN001
    """`from_`/`to` both set to real (non-`None`) dates: any `Or -> And` flip
    on either `... is None or ...` clause makes that clause permanently
    `False` whenever the bound is set, which excludes EVERY day (including
    ones legitimately inside the window) — and `day <= to` weakened to
    `day < to` would drop the trade closed exactly ON `to`."""
    day1, day2, day3, day4 = (dt.date(2026, 7, d) for d in (27, 28, 29, 30))
    with session_factory() as session:
        for i, day in enumerate((day1, day2, day3, day4)):
            session.add(
                _trade(coid=f"c-{i}", closed_at=dt.datetime.combine(day, dt.time(10, 0), tzinfo=dt.UTC), net_paise=100_00)
            )
        session.commit()

    with session_factory() as session:
        curve = equity_curve(session, capital_paise=0, from_=day2, to=day3)

    assert [d for d, _equity in curve] == [day2, day3]
