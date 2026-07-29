"""Repos backing Phase 4's paper-trading loop: cycles/evaluations/
conditions, skipped signals, risk events, open positions (WITH their
mandatory `ExitPlan` persisted alongside), and closed trades.

Every function here takes an already-open `Session` and does NOT commit —
callers own the transaction boundary, matching `te.execution.halt`'s
existing convention (`is_halted`/`set_halt` take a bare `Session`). Only
`te.domain` types cross this module's boundary (never `te.engine` types) —
`te.persistence` sits below `te.engine` in the layer rule, so this module
must not import anything from `te.engine`; converting a persisted row back
into an `te.engine.exits.OpenPosition` is `te.engine.cycle`'s job.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from te.domain.clock import to_utc as _utc
from te.domain.evaluation import ConditionResult, Evaluation
from te.domain.money import Paise
from te.domain.signal import Direction, ExitPlan
from te.persistence.models import (
    CycleEvaluationRow,
    CycleRow,
    EvaluationConditionRow,
    OpenPositionRow,
    RiskEventRow,
    SkippedSignalRow,
    TradeRow,
)


def record_cycle(session: Session, *, ts: dt.datetime, mode: str) -> int:
    row = CycleRow(ts=_utc(ts), mode=mode)
    session.add(row)
    session.flush()
    return row.id


def record_evaluation(session: Session, *, cycle_id: int, evaluation: Evaluation) -> None:
    session.add(
        CycleEvaluationRow(
            cycle_id=cycle_id,
            evaluation_id=evaluation.id,
            ts=_utc(evaluation.timestamp),
            strategy=evaluation.strategy,
            instrument=evaluation.instrument,
            verdict=evaluation.verdict,
            reason=evaluation.reason,
        )
    )
    for seq, condition in enumerate(evaluation.conditions):
        record_condition(session, evaluation.id, seq, condition)


def record_condition(session: Session, evaluation_id: str, seq: int, condition: ConditionResult) -> None:
    session.add(
        EvaluationConditionRow(
            evaluation_id=evaluation_id,
            seq=seq,
            label=condition.label,
            required=condition.required,
            actual=condition.actual,
            passed=condition.passed,
            evaluated=condition.evaluated,
        )
    )


def record_skipped_signal(session: Session, *, ts: dt.datetime, strategy: str, instrument: str, reason: str) -> None:
    """Every skip — including a zero-lots sizing rejection — MUST call this
    with the real reason. `reason` must be non-empty; an empty reason would
    reproduce the exact silent-skip bug this table exists to close."""
    if not reason:
        raise ValueError("record_skipped_signal requires a non-empty reason")
    session.add(SkippedSignalRow(ts=_utc(ts), strategy=strategy, instrument=instrument, reason=reason))


def record_risk_event(session: Session, *, ts: dt.datetime, kind: str, detail: str = "") -> None:
    session.add(RiskEventRow(ts=_utc(ts), kind=kind, detail=detail))


def insert_open_position(
    session: Session,
    *,
    client_order_id: str,
    symbol: str,
    exchange: str,
    strategy: str,
    direction: Direction,
    lots: int,
    lot_size: int,
    entry_premium: Paise,
    exit_plan: ExitPlan,
    opened_at: dt.datetime,
) -> OpenPositionRow:
    row = OpenPositionRow(
        client_order_id=client_order_id,
        symbol=symbol,
        exchange=exchange,
        strategy=strategy,
        direction=direction,
        lots=lots,
        lot_size=lot_size,
        entry_premium_paise=int(entry_premium),
        stop_paise=int(exit_plan.stop),
        current_stop_paise=int(exit_plan.stop),
        trailing_distance_paise=(
            int(exit_plan.trailing_distance) if exit_plan.trailing_distance is not None else None
        ),
        target_paise=int(exit_plan.target),
        max_hold_seconds=int(exit_plan.max_hold.total_seconds()),
        hard_exit_by=exit_plan.hard_exit_by.isoformat(),
        opened_at=_utc(opened_at),
        closed_at=None,
    )
    session.add(row)
    session.flush()
    return row


def open_positions(session: Session) -> list[OpenPositionRow]:
    return list(session.execute(select(OpenPositionRow).where(OpenPositionRow.closed_at.is_(None))).scalars().all())


def open_positions_count(session: Session) -> int:
    return session.execute(
        select(func.count()).select_from(OpenPositionRow).where(OpenPositionRow.closed_at.is_(None))
    ).scalar_one()


def update_trailing_stop(session: Session, row: OpenPositionRow, new_stop: Paise) -> None:
    row.current_stop_paise = int(new_stop)


def mark_position_closed(session: Session, row: OpenPositionRow, *, closed_at: dt.datetime) -> None:
    row.closed_at = _utc(closed_at)


def insert_trade(
    session: Session,
    *,
    client_order_id: str,
    symbol: str,
    exchange: str,
    strategy: str,
    direction: Direction,
    lots: int,
    lot_size: int,
    entry_premium: Paise,
    exit_premium: Paise,
    gross_pnl: Paise,
    costs: Paise,
    net_pnl: Paise,
    exit_reason: str,
    opened_at: dt.datetime,
    closed_at: dt.datetime,
    mode: str = "paper",
    stop_paise: Paise | None = None,
    target_paise: Paise | None = None,
    cycle_evaluation_id: int | None = None,
) -> None:
    """`stop_paise`/`target_paise` are the levels the position was OPENED
    with (the original stop, not a trailed one) — see `TradeRow`. Optional
    only so existing callers and fixtures keep working; the real exit path in
    `te.engine.cycle` always supplies them from the `OpenPositionRow` it is
    closing.

    `cycle_evaluation_id` is accepted but not yet supplied by any caller —
    see `TradeRow.cycle_evaluation_id` for why."""
    session.add(
        TradeRow(
            client_order_id=client_order_id,
            symbol=symbol,
            exchange=exchange,
            strategy=strategy,
            direction=direction,
            lots=lots,
            lot_size=lot_size,
            entry_premium_paise=int(entry_premium),
            exit_premium_paise=int(exit_premium),
            gross_pnl_paise=int(gross_pnl),
            costs_paise=int(costs),
            net_pnl_paise=int(net_pnl),
            exit_reason=exit_reason,
            mode=mode,
            opened_at=_utc(opened_at),
            closed_at=_utc(closed_at),
            stop_paise=None if stop_paise is None else int(stop_paise),
            target_paise=None if target_paise is None else int(target_paise),
            cycle_evaluation_id=cycle_evaluation_id,
        )
    )


def trades_today(session: Session, on: dt.date) -> list[TradeRow]:
    start, end = _day_bounds(on)
    return list(
        session.execute(
            select(TradeRow).where(TradeRow.closed_at >= start, TradeRow.closed_at <= end)
        ).scalars().all()
    )


def _day_bounds(on: dt.date) -> tuple[dt.datetime, dt.datetime]:
    return (
        dt.datetime.combine(on, dt.time.min, tzinfo=dt.UTC),
        dt.datetime.combine(on, dt.time.max, tzinfo=dt.UTC),
    )


def trades_count_today(session: Session, on: dt.date) -> int:
    """Aggregated in SQL — this runs once per instrument per cycle via
    `te.risk.limits`, and hydrating the whole day's `TradeRow` objects just
    to take their length is pure waste."""
    start, end = _day_bounds(on)
    return session.execute(
        select(func.count()).select_from(TradeRow).where(TradeRow.closed_at >= start, TradeRow.closed_at <= end)
    ).scalar_one()


def daily_net_pnl_paise(session: Session, on: dt.date) -> Paise:
    """Aggregated in SQL — see `trades_count_today`. `func.sum` is NULL (not
    0) over an empty set, hence the coalesce."""
    start, end = _day_bounds(on)
    total = session.execute(
        select(func.coalesce(func.sum(TradeRow.net_pnl_paise), 0)).where(
            TradeRow.closed_at >= start, TradeRow.closed_at <= end
        )
    ).scalar_one()
    return Paise(int(total))


def recent_trades(session: Session, *, limit: int = 500) -> list[TradeRow]:
    """The most recent `limit` CLOSED trades, newest first — feeds
    `te.risk.monitors.RollingPerformance` (Tier 3, informational only)."""
    return list(
        session.execute(select(TradeRow).order_by(TradeRow.closed_at.desc()).limit(limit)).scalars().all()
    )


def recent_session_dates(session: Session, *, limit: int) -> list[dt.date]:
    """The `limit` most recent distinct trading dates that have a CLOSED
    trade, newest first. Aggregated in SQL so a caller wanting a trailing
    N-SESSION window can derive its cutoff date without first hydrating
    every trade row in the history."""
    rows = session.execute(
        select(func.date(TradeRow.closed_at))
        .distinct()
        .order_by(func.date(TradeRow.closed_at).desc())
        .limit(limit)
    ).scalars().all()
    return [dt.date.fromisoformat(str(row)) for row in rows]


def trades_closed_since(session: Session, cutoff: dt.date) -> list[TradeRow]:
    """Every CLOSED trade on or after `cutoff` (a DATE, inclusive), newest
    first. Paired with `recent_session_dates` to fetch exactly the rows a
    trailing-window statistic actually uses, and no others."""
    start = dt.datetime.combine(cutoff, dt.time.min, tzinfo=dt.UTC)
    return list(
        session.execute(
            select(TradeRow).where(TradeRow.closed_at >= start).order_by(TradeRow.closed_at.desc())
        ).scalars().all()
    )
