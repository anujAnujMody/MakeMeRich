"""Daily loss limit, max concurrent positions, max trades/day — all
PERSISTED checks, not in-memory counters. Every check reads its counter
straight from SQLite (`trades`/`open_positions`), so it survives a process
restart by construction: a fresh session against the same DB file sees the
same counts a pre-restart process would have.

A daily-loss breach additionally sets the halt flag (`te.execution.halt`,
the existing DB-flag layer `te.risk.killswitch` also reads) so a tripped
daily loss limit keeps blocking new orders across a restart too.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy.orm import Session

from te.domain.money import Paise
from te.execution.halt import set_halt
from te.persistence.repos.paper_trading import (
    daily_net_pnl_paise,
    open_positions_count,
    record_risk_event,
    trades_count_today,
)


@dataclass(frozen=True)
class RiskLimitsConfig:
    max_daily_loss_paise: Paise
    max_concurrent_positions: int
    max_trades_per_day: int


class LimitBreachError(Exception):
    def __init__(self, kind: str, reason: str) -> None:
        self.kind = kind
        self.reason = reason
        super().__init__(reason)


def check_daily_loss_limit(session: Session, config: RiskLimitsConfig, *, on: dt.date, now: dt.datetime) -> None:
    net = daily_net_pnl_paise(session, on)
    if net <= -config.max_daily_loss_paise:
        reason = (
            f"daily net P&L ({net}p) breached the daily loss limit "
            f"(-{config.max_daily_loss_paise}p) on {on.isoformat()}"
        )
        record_risk_event(session, ts=now, kind="daily_loss_halt", detail=reason)
        set_halt(session, reason)
        raise LimitBreachError("daily_loss", reason)


def check_max_concurrent_positions(session: Session, config: RiskLimitsConfig) -> None:
    count = open_positions_count(session)
    if count >= config.max_concurrent_positions:
        raise LimitBreachError(
            "max_positions",
            f"{count} position(s) already open, at or above the limit of {config.max_concurrent_positions}",
        )


def check_max_trades_per_day(session: Session, config: RiskLimitsConfig, *, on: dt.date) -> None:
    count = trades_count_today(session, on)
    if count >= config.max_trades_per_day:
        raise LimitBreachError(
            "max_trades",
            f"{count} trade(s) already placed today, at or above the limit of {config.max_trades_per_day}",
        )


def check_all(session: Session, config: RiskLimitsConfig, *, on: dt.date, now: dt.datetime) -> None:
    """Runs every persisted check, in the order a real cycle would care
    about most: has today already gone bad enough to halt, are we already
    at max concurrent exposure, have we already traded enough today."""
    check_daily_loss_limit(session, config, on=on, now=now)
    check_max_concurrent_positions(session, config)
    check_max_trades_per_day(session, config, on=on)
