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
from decimal import Decimal

from sqlalchemy.orm import Session

from te.domain.money import Paise
from te.engine.state import get_peak_equity_paise, set_peak_equity_paise
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
    #: `Decimal(100)` (no-op — never breaches) by default so every existing
    #: call site/test that predates this guardrail keeps behaving exactly as
    #: before. See `check_max_drawdown`.
    max_drawdown_pct: Decimal = Decimal(100)


class LimitBreachError(Exception):
    def __init__(self, kind: str, reason: str) -> None:
        self.kind = kind
        self.reason = reason
        super().__init__(reason)


#: Default for `unrealized_pnl_paise` params below — realized-only checking,
#: matching this module's historical behaviour when no mark is available.
_ZERO_PAISE = Paise(0)


def check_daily_loss_limit(
    session: Session,
    config: RiskLimitsConfig,
    *,
    on: dt.date,
    now: dt.datetime,
    unrealized_pnl_paise: Paise = _ZERO_PAISE,
) -> None:
    """`unrealized_pnl_paise` is the sum of every open position's
    mark-to-market P&L (negative when underwater), computed by the caller
    via `te.domain.pnl.mark_to_market_pnl` over `open_positions()` — kept
    an argument rather than queried here because pricing a mark needs a
    `BarStore` + `CostModel`, both above this module in the layer rule.
    Defaults to `0` (realized-only, the historical behaviour) when the
    caller has no mark available. Without this, an unattended session could
    run unlimited unrealized drawdown across open positions — losses that
    haven't been booked into a closed `Trade` yet — without ever tripping
    the halt that is supposed to protect capital."""
    net = daily_net_pnl_paise(session, on) + unrealized_pnl_paise
    if net <= -config.max_daily_loss_paise:
        reason = (
            f"daily net P&L including open positions ({net}p) breached the daily loss limit "
            f"(-{config.max_daily_loss_paise}p) on {on.isoformat()}"
        )
        record_risk_event(session, ts=now, kind="daily_loss_halt", detail=reason)
        set_halt(session, reason)
        raise LimitBreachError("daily_loss", reason)


def check_max_drawdown(
    session: Session, config: RiskLimitsConfig, *, now: dt.datetime, current_equity_paise: Paise
) -> None:
    """Account-level drawdown: `current_equity_paise` (capital + lifetime
    realized net P&L + unrealized P&L on open positions — computed by the
    caller, `te.engine.cycle.run_entry_cycle`, since pricing a mark needs a
    `BarStore`/`CostModel` both above this module in the layer rule)
    against a peak-equity watermark (`te.engine.state.get_peak_equity_paise`
    /`set_peak_equity_paise`), ratcheted up (never down) on every call.
    Halts when the drop from peak exceeds `max_drawdown_pct`.

    Distinct from `te.risk.monitors.DrawdownEnvelope` (Tier 2 of the Phase 7
    decay monitors), which compares against a STORED backtest-bootstrapped
    percentile that does not exist yet (no real backtest has produced one) —
    this check needs no such precomputed envelope, so it's usable today
    rather than staying an unenforced guardrail field indefinitely. Found
    live: `max_drawdown_pct` was editable and displayed in Settings but
    enforced nowhere."""
    stored_peak = get_peak_equity_paise(session)
    peak = Paise(max(int(stored_peak), int(current_equity_paise))) if stored_peak is not None else current_equity_paise
    if stored_peak is None or peak != stored_peak:
        set_peak_equity_paise(session, peak)

    if peak <= 0:
        return  # no meaningful watermark yet (e.g. capital itself is 0 in a test) — nothing to compare against

    drawdown_pct = Decimal(peak - current_equity_paise) / Decimal(peak) * Decimal(100)
    if drawdown_pct >= config.max_drawdown_pct:
        reason = (
            f"current equity ({current_equity_paise}p) is {drawdown_pct:.2f}% below its peak "
            f"({peak}p), breaching max_drawdown_pct ({config.max_drawdown_pct}%)"
        )
        record_risk_event(session, ts=now, kind="max_drawdown_halt", detail=reason)
        set_halt(session, reason)
        raise LimitBreachError("max_drawdown", reason)


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
