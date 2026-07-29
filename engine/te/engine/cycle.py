"""The engine cycle — fetch (via `bars_asof`) -> strategy.evaluate -> risk.
size_position -> decide -> act (place via `ExecutionManager.submit` against
`SimulatedBroker` for paper mode) -> persist the `Evaluation`/
`ConditionResult`s. Every skip — including a zero-lots sizing rejection —
persists a `SkippedSignal` row with the real reason, per the plan's
explicit call-out that this is the exact bug class that meant the old
engine silently never traded.

Two entry points, run every cycle:
- `run_entry_cycle()` — evaluates each configured instrument for a new
  signal, sizes it, and (if everything passes) opens a position with a
  mandatory `ExitPlan` attached.
- `run_exit_cycle()` — evaluates exits on every currently open position via
  `te.engine.exits`, closing (and recording a net-of-cost `Trade`) any
  position whose stop/trailing-stop/target/time fired.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from te.data.barstore import BarStore
from te.domain.clock import assume_utc as _as_utc
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.domain.orders import OrderRequest
from te.domain.pnl import GrossPnl, net_pnl
from te.domain.signal import ExitPlan, Signal
from te.engine.exits import OpenPosition, evaluate_position
from te.execution.manager import ExecutionManager
from te.ml.gates import MLHook, MLInfluence
from te.persistence.db import session_scope
from te.persistence.models import OpenPositionRow
from te.persistence.repos.paper_trading import (
    insert_open_position,
    insert_trade,
    mark_position_closed,
    open_positions,
    record_cycle,
    record_evaluation,
    record_skipped_signal,
    update_trailing_stop,
)
from te.risk.killswitch import KillSwitchTrippedError, is_currently_throttled
from te.risk.killswitch import check as check_killswitch
from te.risk.limits import LimitBreachError, RiskLimitsConfig
from te.risk.limits import check_all as check_risk_limits
from te.risk.regime import DEFAULT_REGIME_THROTTLE_CONFIG, compose_size_multipliers
from te.risk.sizing import size_position
from te.strategy.context import StrategyContext
from te.strategy.registry import get as get_strategy


@dataclass(frozen=True)
class CycleConfig:
    mode: str
    strategy_name: str
    instruments: tuple[str, ...]
    exchange: str
    lot_size: int
    capital: Paise
    risk_budget_pct: Decimal
    min_edge_multiple: Decimal
    stop_distance: Paise
    target_distance: Paise
    trailing_distance: Paise | None
    max_hold: dt.timedelta
    hard_exit_by: dt.time
    risk_limits: RiskLimitsConfig


def _row_to_position(row: OpenPositionRow) -> OpenPosition:
    exit_plan = ExitPlan(
        stop=Paise(row.stop_paise),
        trailing_distance=Paise(row.trailing_distance_paise) if row.trailing_distance_paise is not None else None,
        target=Paise(row.target_paise),
        max_hold=dt.timedelta(seconds=row.max_hold_seconds),
        hard_exit_by=dt.time.fromisoformat(row.hard_exit_by),
    )
    return OpenPosition(
        symbol=row.symbol,
        exchange=row.exchange,
        strategy=row.strategy,
        direction=row.direction,  # type: ignore[arg-type]
        entry_premium=Paise(row.entry_premium_paise),
        lot_size=row.lot_size,
        lots=row.lots,
        opened_at=_as_utc(row.opened_at),
        exit_plan=exit_plan,
        current_stop=Paise(row.current_stop_paise),
    )


def run_entry_cycle(
    *,
    session_factory: sessionmaker[Session],
    store: BarStore,
    execution: ExecutionManager,
    cost_model: CostModel,
    config: CycleConfig,
    as_of: dt.datetime,
    ml_hook: MLHook | None = None,
) -> int:
    """One fetch->analyze->risk->decide->act pass across every configured
    instrument. Returns the persisted `cycle_id`.

    `ml_hook` is OPTIONAL and, when supplied, is the only way `te.ml` can
    touch this function — it returns an `MLInfluence`, never a raw model
    probability; this module never imports `te.ml.model.MetaModel` and
    never sees `p`. When `ml_hook` is `None` (the default — matches every
    call site before Phase 6), behaviour is byte-identical to before this
    parameter existed. Below `gating` stage (`shadow`/`advisory` — the only
    stage this phase operationally exercises), `ml_hook.evaluate()` always
    returns `MLInfluence(size_multiplier=1, veto=False,
    displayed_verdict=...)`, so even when `ml_hook` IS supplied, the
    resulting decision is unchanged from the rule-only decision — see
    `tests/engine/test_cycle_ml_gate.py::test_ml_cannot_affect_decisions_below_gating`.
    """
    with session_scope(session_factory) as session:
        cycle_id = record_cycle(session, ts=as_of, mode=config.mode)

    def _skip(instrument: str, reason: str) -> None:
        """Every early exit from the per-instrument loop below persists a
        `SkippedSignal` with the REAL reason — the plan's explicit
        call-out that a silently-swallowed skip is the exact bug class that
        meant the old engine never traded."""
        with session_scope(session_factory) as session:
            record_skipped_signal(
                session, ts=as_of, strategy=config.strategy_name, instrument=instrument, reason=reason
            )

    for instrument in config.instruments:
        strategy = get_strategy(config.strategy_name)
        ctx = StrategyContext(store=store, instrument=instrument, exchange=config.exchange, as_of=as_of)
        evaluation = strategy.evaluate(ctx)

        with session_scope(session_factory) as session:
            record_evaluation(session, cycle_id=cycle_id, evaluation=evaluation)

        if evaluation.verdict != "traded":
            _skip(instrument, evaluation.reason)
            continue

        # `Strategy` (the Protocol) deliberately only declares `name`/
        # `evaluate()` per the plan's exact signature; `last_signal` is an
        # informal extension a rule MAY set when it trades (te.strategy.orb
        # does). Read it defensively via getattr rather than widening the
        # Protocol.
        signal: Signal | None = getattr(strategy, "last_signal", None)
        if signal is None:
            # Defensive only — `evaluate()` traded => `last_signal` is set,
            # per te.strategy.orb's contract. Treat as a skip rather than
            # crash the whole cycle if a future Strategy implementation
            # ever violates that contract.
            _skip(instrument, "strategy reported verdict=traded but produced no Signal")
            continue

        throttled = False
        blocked_reason: str | None = None
        with session_scope(session_factory) as session:
            try:
                check_killswitch(session)
                check_risk_limits(session, config.risk_limits, on=as_of.date(), now=as_of)
            except (KillSwitchTrippedError, LimitBreachError) as exc:
                # Recorded AFTER this session closes (via `_skip`) rather
                # than on this session, so the skip write never nests one
                # SQLite write transaction inside another.
                blocked_reason = str(exc)
            else:
                # Phase 7's throttle (`te.risk.killswitch.throttle()`) is a
                # "reduce size" signal, distinct from a halt — read fresh
                # from the DB each cycle (see killswitch.py's module
                # docstring for why there is no in-process throttle cache)
                # and compose it below with any ML size multiplier via
                # `te.risk.regime.compose_size_multipliers`, never a halt.
                throttled = is_currently_throttled(session)
        if blocked_reason is not None:
            _skip(instrument, blocked_reason)
            continue

        stop_premium = Paise(signal.entry_premium - config.stop_distance)
        target_premium = Paise(signal.entry_premium + config.target_distance)

        sizing = size_position(
            capital=config.capital,
            risk_budget_pct=config.risk_budget_pct,
            premium=signal.entry_premium,
            stop_premium=stop_premium,
            target_premium=target_premium,
            lot_size=config.lot_size,
            costs=cost_model,
            exchange=config.exchange,
            on=as_of.date(),
            min_edge_multiple=config.min_edge_multiple,
        )
        if sizing.lots == 0:
            _skip(instrument, sizing.rejected_reason or "sizing rejected with no reason (bug)")
            continue

        # `ml_hook` is the ONLY place `te.ml` can touch this decision — see
        # this function's docstring. Below `gating` stage this is always
        # `MLInfluence(1, False, ...)`, so `lots`/the decision to trade are
        # unchanged; this block is a structural no-op in shadow/advisory.
        influence = MLInfluence(size_multiplier=Decimal(1), veto=False, displayed_verdict=None)
        if ml_hook is not None:
            influence = ml_hook.evaluate(instrument=instrument, as_of=as_of, cycle_id=cycle_id)

        if influence.veto:
            _skip(instrument, "ML maturity gate vetoed this signal")
            continue

        # `throttle_multiplier` is `1` unless a Phase 7 monitor
        # (`te.risk.monitors`) has thrown the DB-only throttle flag this
        # cycle; when throttled it reuses `RegimeThrottleConfig`'s
        # top-tercile ("elevated/crisis") multiplier as the reduction —
        # composed with the ML multiplier via `compose_size_multipliers`,
        # never applied by widening `size_position()`'s own signature.
        throttle_multiplier = DEFAULT_REGIME_THROTTLE_CONFIG.high_tercile_multiplier if throttled else Decimal(1)
        combined_multiplier = compose_size_multipliers(throttle_multiplier, influence.size_multiplier)

        lots = sizing.lots
        if combined_multiplier != 1:
            lots = int(Decimal(sizing.lots) * combined_multiplier)
            if lots < 1:
                _skip(
                    instrument,
                    "throttle/ML size multiplier resized position below 1 lot "
                    f"(combined_multiplier={combined_multiplier}, throttled={throttled}, "
                    f"ml_multiplier={influence.size_multiplier})",
                )
                continue

        request = OrderRequest(
            symbol=instrument,
            exchange=config.exchange,
            side="BUY",
            quantity=config.lot_size * lots,
            order_type="LIMIT",
            limit_price=signal.entry_premium,
        )
        client_order_id = execution.submit(request)

        exit_plan = ExitPlan(
            stop=stop_premium,
            trailing_distance=config.trailing_distance,
            target=target_premium,
            max_hold=config.max_hold,
            hard_exit_by=config.hard_exit_by,
        )
        with session_scope(session_factory) as session:
            insert_open_position(
                session,
                client_order_id=client_order_id,
                symbol=instrument,
                exchange=config.exchange,
                strategy=config.strategy_name,
                direction=signal.direction,
                lots=lots,
                lot_size=config.lot_size,
                entry_premium=signal.entry_premium,
                exit_plan=exit_plan,
                opened_at=as_of,
            )

    return cycle_id


def run_exit_cycle(
    *,
    session_factory: sessionmaker[Session],
    execution: ExecutionManager,
    cost_model: CostModel,
    current_premium: Callable[[OpenPositionRow], Paise],
    as_of: dt.datetime,
) -> list[str]:
    """Evaluates exits on every currently open position. `current_premium`
    supplies the live mark for one position's symbol (the caller's job to
    wire to a real quote — kept a callable here so this stays testable
    without a broker/quote feed). Returns the `client_order_id`s of every
    position closed this cycle. Not gated by the kill switch — exiting a
    position is risk-REDUCING and should still be able to run during a
    halt; only new entries (`run_entry_cycle`) are blocked."""
    closed: list[str] = []
    # ONE session for the whole loop: the rows `open_positions()` returns are
    # already the live ORM objects to write through, so there is no need to
    # re-`session.get()` each one in a fresh session per position.
    with session_scope(session_factory) as session:
        rows = open_positions(session)
        for row in rows:
            position = _row_to_position(row)
            premium = current_premium(row)
            updated, decision = evaluate_position(position, current_premium=premium, now=as_of)

            if decision is None:
                # Only write when the trailing stop actually ratcheted —
                # an unchanged stop is the common case every cycle.
                if int(updated.current_stop) != row.current_stop_paise:
                    update_trailing_stop(session, row, updated.current_stop)
                continue

            qty = row.lots * row.lot_size
            request = OrderRequest(
                symbol=row.symbol,
                exchange=row.exchange,
                side="SELL",
                quantity=qty,
                order_type="LIMIT",
                limit_price=decision.exit_premium,
            )
            # Flush+commit anything pending before the broker round-trip:
            # `execution.submit` opens its own session, and holding this
            # one's write transaction open across that call would have one
            # SQLite writer waiting on another.
            session.commit()
            execution.submit(request)

            entry_premium = Paise(row.entry_premium_paise)
            costs = cost_model.round_trip(
                entry_premium=entry_premium,
                exit_premium=decision.exit_premium,
                qty=qty,
                exchange=row.exchange,
                on=as_of.date(),
            )
            gross = GrossPnl(Paise((decision.exit_premium - entry_premium) * qty))
            net = net_pnl(entry_premium, decision.exit_premium, qty, costs)

            mark_position_closed(session, row, closed_at=as_of)
            insert_trade(
                session,
                client_order_id=row.client_order_id,
                symbol=row.symbol,
                exchange=row.exchange,
                strategy=row.strategy,
                direction=row.direction,  # type: ignore[arg-type]
                lots=row.lots,
                lot_size=row.lot_size,
                entry_premium=entry_premium,
                exit_premium=decision.exit_premium,
                gross_pnl=Paise(gross),
                costs=costs.total,
                net_pnl=Paise(net),
                exit_reason=decision.reason,
                opened_at=_as_utc(row.opened_at),
                closed_at=as_of,
                mode="paper",
                # The levels the position was OPENED with — `row.stop_paise`,
                # never the trailed `row.current_stop_paise`. `row` is about
                # to become a closed `open_positions` row, so this is the last
                # point at which they can be carried onto the trade record.
                stop_paise=Paise(row.stop_paise),
                target_paise=Paise(row.target_paise),
            )
            session.commit()
            closed.append(row.client_order_id)

    return closed
