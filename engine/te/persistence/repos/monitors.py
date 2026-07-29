"""Repos backing Phase 7's `te/risk/monitors.py` — Tier 0 slippage
observations, Tier 1 CUSUM state, and the Tier 2 persisted drawdown
envelope. Same convention as `te.persistence.repos.paper_trading`: every
function takes an already-open `Session` and does NOT commit — callers own
the transaction boundary.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from te.domain.clock import to_utc as _utc
from te.persistence.models import (
    BacktestDrawdownEnvelopeRow,
    MonitorStateRow,
    SlippageObservationRow,
)


def insert_slippage_observation(
    session: Session,
    *,
    ts: dt.datetime,
    instrument: str,
    expected_paise: int,
    actual_paise: int,
    context: str,
) -> None:
    session.add(
        SlippageObservationRow(
            ts=_utc(ts),
            instrument=instrument,
            expected_paise=expected_paise,
            actual_paise=actual_paise,
            diff_paise=actual_paise - expected_paise,
            context=context,
        )
    )


def recent_slippage_observations(session: Session, *, instrument: str, limit: int) -> list[SlippageObservationRow]:
    """The most recent `limit` observations for `instrument`, OLDEST first
    (chronological order, matching how a rolling window is normally read)."""
    rows = (
        session.execute(
            select(SlippageObservationRow)
            .where(SlippageObservationRow.instrument == instrument)
            .order_by(SlippageObservationRow.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


def get_monitor_state(session: Session, key: str) -> MonitorStateRow | None:
    return session.get(MonitorStateRow, key)


def upsert_monitor_state(session: Session, key: str, *, s_pos: float, s_neg: float, last_action: str) -> None:
    row = session.get(MonitorStateRow, key)
    now = dt.datetime.now(dt.UTC)
    if row is None:
        session.add(MonitorStateRow(key=key, s_pos=s_pos, s_neg=s_neg, last_action=last_action, updated_at=now))
    else:
        row.s_pos = s_pos
        row.s_neg = s_neg
        row.last_action = last_action
        row.updated_at = now


def save_drawdown_envelope(session: Session, *, run_id: str, percentiles: dict[float, int]) -> None:
    """Persists one backtest run's bootstrapped drawdown envelope —
    computed ONCE by `te.backtest.report.stationary_bootstrap_drawdown_envelope`
    at backtest-report time, never recomputed by `DrawdownEnvelope.check()`."""
    now = dt.datetime.now(dt.UTC)
    for percentile, drawdown_paise in percentiles.items():
        session.add(
            BacktestDrawdownEnvelopeRow(
                run_id=run_id, percentile=percentile, drawdown_paise=drawdown_paise, created_at=now
            )
        )


def load_drawdown_envelope(session: Session, run_id: str) -> dict[float, int]:
    rows = (
        session.execute(select(BacktestDrawdownEnvelopeRow).where(BacktestDrawdownEnvelopeRow.run_id == run_id))
        .scalars()
        .all()
    )
    return {row.percentile: row.drawdown_paise for row in rows}
