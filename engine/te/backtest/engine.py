"""`run_backtest()` — a bar-close event loop over `te.data.barstore.BarStore`
via `te.data.asof.bars_asof`/`te.strategy.context.StrategyContext`, zero
lookahead BY CONSTRUCTION: this loop adds no window logic of its own, it
simply steps `timestamps` forward and delegates every bar read to
`bars_asof` (the point-in-time gate already proven in Phase 1) — exactly the
same read path live/paper trading uses. Drives `te.strategy.orb.OrbStrategy`
(or any `Strategy`) bar-by-bar for entries and `te.engine.exits.
evaluate_position()` for exits on the currently open position, filling every
order through `te.backtest.fills.BacktestFillEngine` (the shared
`SimulatedBroker` path with paper trading — see that module's docstring).

Deliberately single-position (one open position at a time) for this phase —
matches the plan's "directional option buying only" scope and keeps the
event loop simple enough to audit for lookahead by inspection; multi-
position concurrency is `te.engine.cycle`'s job for live/paper, not
backtest's validation-machinery job.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

from te.backtest.fills import BacktestFillEngine
from te.data.asof import bars_asof
from te.data.barstore import BarStore
from te.domain.clock import IST
from te.domain.costs import CostBreakdown, CostModel
from te.domain.geometry import ExitGeometry, RupeeRiskGeometry
from te.domain.money import Paise
from te.domain.orders import OrderIntent
from te.domain.pnl import GrossPnl, NetPnl, net_pnl
from te.domain.signal import Direction, ExitPlan
from te.engine.exits import ExitReason, OpenPosition, evaluate_position, open_position
from te.risk.sizing import size_position
from te.strategy.base import Strategy
from te.strategy.context import StrategyContext


@dataclass(frozen=True)
class BacktestConfig:
    """Mirrors the sizing/exit-plan fields of `te.engine.cycle.CycleConfig`
    — deliberately the same shape, since a backtest run is meant to
    reproduce exactly what the live/paper cycle would have decided."""

    capital: Paise
    risk_budget_pct: Decimal
    min_edge_multiple: Decimal
    #: THE SAME type `CycleConfig` holds, deliberately. The two configs
    #: previously restated exit levels as six separate fields each, and the
    #: newer live rules simply never arrived here — so a backtest measured a
    #: strategy the engine does not trade, which is exactly what this class's
    #: docstring promises cannot happen.
    exit_geometry: ExitGeometry
    max_hold: dt.timedelta
    hard_exit_by: dt.time
    lot_size: int
    #: Mirrors `CycleConfig`. A firing too close to the hard exit carries
    #: full downside against upside that is unreachable by construction; a
    #: backtest that still takes those trades overstates the strategy.
    min_minutes_before_hard_exit: int = 0
    #: Mirrors `CycleConfig`'s over-trading guard.
    max_entries_per_underlying_per_day: int = 2

    def __post_init__(self) -> None:
        if isinstance(self.exit_geometry, RupeeRiskGeometry):
            # Unlike `CycleConfig`, this class has no `max_lots` field, so
            # `_step_entry`/`_step_exit` below call `exit_geometry.levels()`
            # with NO `quantity=` at all. `RupeeRiskGeometry.levels()`
            # requires a positive quantity and would raise immediately on
            # the first entry it evaluates — refused here, at construction,
            # with a clear reason instead of a confusing crash mid-backtest.
            raise ValueError(
                "BacktestConfig does not support RupeeRiskGeometry — this backtest has no max_lots/quantity "
                "concept to convert a rupee cap into a premium distance the way te.engine.cycle.CycleConfig "
                "does; use PremiumPercentGeometry or AbsolutePointGeometry here instead"
            )


@dataclass(frozen=True)
class ClosedTrade:
    symbol: str
    exchange: str
    strategy: str
    direction: Direction
    lots: int
    lot_size: int
    entry_premium: Paise
    exit_premium: Paise
    entry_ts: dt.datetime
    exit_ts: dt.datetime
    exit_reason: ExitReason
    gross_pnl: GrossPnl
    costs: CostBreakdown
    net_pnl: NetPnl


@dataclass(frozen=True)
class BacktestResult:
    trades: tuple[ClosedTrade, ...]


def _last_close_premium(store: BarStore, symbol: str, as_of: dt.datetime, interval: str) -> Paise | None:
    """Same conversion `te.strategy.orb.OrbStrategy` uses (bar close in
    index points -> paise) — this phase doesn't yet track a separate option
    premium bar series (see the plan's Phase 1 scope), so the underlying's
    own recorded bars stand in as the premium proxy, consistently, on both
    the entry and exit side."""
    bars = bars_asof(store, symbol, as_of, dt.timedelta(minutes=1), interval=interval)
    if bars.empty:
        return None
    return Paise(int(round(float(bars.iloc[-1]["c"]) * 100)))


def run_backtest(
    *,
    store: BarStore,
    instrument: str,
    exchange: str,
    strategy: Strategy,
    cost_model: CostModel,
    fills: BacktestFillEngine,
    config: BacktestConfig,
    timestamps: list[dt.datetime],
    interval: str = "1m",
) -> BacktestResult:
    """Steps `timestamps` (bar-close points, in increasing order) one at a
    time. At each `as_of`: if a position is open, evaluate its exit;
    otherwise evaluate the strategy for a new entry. Every read goes through
    `bars_asof(..., as_of=as_of)`, so nothing at step `k` can ever see a bar
    that closes after `timestamps[k]` — the zero-lookahead guarantee."""
    trades: list[ClosedTrade] = []
    open_pos: OpenPosition | None = None
    # Mirrors `underlying_entries_today` (`te/persistence/repos/paper_trading.py`)
    # for the `max_entries_per_underlying_per_day` guard below — a backtest
    # has no DB session in this phase, so the count is kept in memory here,
    # keyed on the IST calendar date, and never reset except by moving to a
    # new day.
    entries_today_by_date: dict[dt.date, int] = {}

    for as_of in timestamps:
        if open_pos is not None:
            open_pos, closed = _step_exit(
                open_pos, store=store, instrument=instrument, as_of=as_of, interval=interval,
                cost_model=cost_model, fills=fills,
            )
            if closed is not None:
                trades.append(closed)
            continue

        day = as_of.astimezone(IST).date()
        open_pos = _step_entry(
            store=store, instrument=instrument, exchange=exchange, strategy=strategy, as_of=as_of,
            interval=interval, cost_model=cost_model, fills=fills, config=config,
            entries_today=entries_today_by_date.get(day, 0),
        )
        if open_pos is not None:
            entries_today_by_date[day] = entries_today_by_date.get(day, 0) + 1

    return BacktestResult(trades=tuple(trades))


def _step_exit(
    position: OpenPosition,
    *,
    store: BarStore,
    instrument: str,
    as_of: dt.datetime,
    interval: str,
    cost_model: CostModel,
    fills: BacktestFillEngine,
) -> tuple[OpenPosition | None, ClosedTrade | None]:
    premium = _last_close_premium(store, instrument, as_of, interval)
    if premium is None:
        return position, None

    updated, decision = evaluate_position(position, current_premium=premium, now=as_of)
    if decision is None:
        return updated, None

    qty = updated.lots * updated.lot_size
    exit_intent = OrderIntent(
        client_order_id=f"bt-exit-{uuid.uuid4().hex[:12]}",
        symbol=updated.symbol,
        exchange=updated.exchange,
        side="SELL",
        quantity=qty,
        order_type="LIMIT",
        limit_price=decision.exit_premium,
        ts=as_of,
    )
    fill = fills.fill(exit_intent)

    costs = cost_model.round_trip(
        entry_premium=updated.entry_premium, exit_premium=fill.fill_price, qty=qty, exchange=updated.exchange,
        on=as_of.date(),
    )
    gross = GrossPnl(Paise((fill.fill_price - updated.entry_premium) * qty))
    net = net_pnl(updated.entry_premium, fill.fill_price, qty, costs)

    closed = ClosedTrade(
        symbol=updated.symbol, exchange=updated.exchange, strategy=updated.strategy, direction=updated.direction,
        lots=updated.lots, lot_size=updated.lot_size, entry_premium=updated.entry_premium,
        exit_premium=fill.fill_price, entry_ts=updated.opened_at, exit_ts=as_of, exit_reason=decision.reason,
        gross_pnl=gross, costs=costs, net_pnl=net,
    )
    return None, closed


def _step_entry(
    *,
    store: BarStore,
    instrument: str,
    exchange: str,
    strategy: Strategy,
    as_of: dt.datetime,
    interval: str,
    cost_model: CostModel,
    fills: BacktestFillEngine,
    config: BacktestConfig,
    entries_today: int,
) -> OpenPosition | None:
    # Mirrors `te.engine.cycle.run_once`'s `min_minutes_before_hard_exit`
    # portfolio gate (`te/engine/cycle.py:436-447`) exactly, including the
    # `>=` boundary: a firing too close to the hard exit carries full
    # downside against upside that is unreachable by construction. Checked
    # BEFORE the strategy is even evaluated, same as live.
    now_ist_time = as_of.astimezone(IST).timetz().replace(tzinfo=None)
    latest_entry = (
        dt.datetime.combine(dt.date.min, config.hard_exit_by)
        - dt.timedelta(minutes=config.min_minutes_before_hard_exit)
    ).time()
    if now_ist_time >= latest_entry:
        return None

    ctx = StrategyContext(store=store, instrument=instrument, exchange=exchange, as_of=as_of, interval=interval)
    evaluation = strategy.evaluate(ctx)
    if evaluation.verdict != "traded":
        return None

    signal = getattr(strategy, "last_signal", None)
    if signal is None:
        return None

    # Mirrors `te.engine.cycle.run_once`'s max-entries-per-underlying-per-day
    # guard (`te/engine/cycle.py:529-539`), including the `>=` boundary.
    if entries_today >= config.max_entries_per_underlying_per_day:
        return None

    levels = config.exit_geometry.levels(signal.entry_premium)
    stop_premium, target_premium = levels.stop, levels.target
    sizing = size_position(
        capital=config.capital, risk_budget_pct=config.risk_budget_pct, premium=signal.entry_premium,
        stop_premium=stop_premium, target_premium=target_premium, lot_size=config.lot_size, costs=cost_model,
        exchange=exchange, on=as_of.date(), min_edge_multiple=config.min_edge_multiple,
    )
    if sizing.lots == 0:
        return None

    entry_intent = OrderIntent(
        client_order_id=f"bt-entry-{uuid.uuid4().hex[:12]}", symbol=instrument, exchange=exchange, side="BUY",
        quantity=config.lot_size * sizing.lots, order_type="LIMIT", limit_price=signal.entry_premium, ts=as_of,
    )
    fill = fills.fill(entry_intent)

    # Levels re-derived from the ACTUAL fill, not the signal price, so the
    # plan's barriers and its `entry_premium` describe the same trade.
    filled = config.exit_geometry.levels(fill.fill_price)
    # `filled.profit_lock_activation`/`profit_lock_buffer_pct` were computed
    # by the geometry but previously discarded here, so `evaluate_position`
    # (the SAME live exit-evaluation function this backtest already calls
    # below, via `_step_exit`) always saw them as `None` and the rule could
    # never fire in a backtest even when the live cycle would have engaged
    # it — see `te.engine.exits.evaluate_position`'s profit-lock block.
    exit_plan = ExitPlan(
        entry_premium=fill.fill_price, stop=filled.stop, trailing_distance=filled.trailing_distance,
        target=filled.target, max_hold=config.max_hold, hard_exit_by=config.hard_exit_by,
        profit_lock_activation=filled.profit_lock_activation,
        profit_lock_buffer_pct=filled.profit_lock_buffer_pct,
    )
    return open_position(
        symbol=instrument, exchange=exchange, strategy=strategy.name, direction=signal.direction,
        lot_size=config.lot_size, lots=sizing.lots, opened_at=as_of, exit_plan=exit_plan,
    )
