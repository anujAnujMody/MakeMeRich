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
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from te.data.asof import latest_close_paise
from te.data.barstore import BarStore
from te.domain.clock import IST
from te.domain.clock import assume_utc as _as_utc
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.domain.orders import OrderRequest
from te.domain.pnl import GrossPnl, mark_to_market_pnl, net_pnl
from te.domain.signal import ExitPlan, Signal, trailing_activation_for
from te.engine.contract import ContractResolver
from te.engine.exits import OpenPosition, evaluate_position
from te.engine.state import PIPELINE_STAGE_KEYS, PipelineStageTiming, set_last_cycle_pipeline
from te.execution.manager import ExecutionManager
from te.ml.gates import MLHook, MLInfluence
from te.persistence.db import session_scope
from te.persistence.models import OpenPositionRow
from te.persistence.repos.paper_trading import (
    find_open_position,
    insert_open_position,
    insert_trade,
    mark_position_closed,
    open_positions,
    record_cycle,
    record_evaluation,
    record_skipped_signal,
    total_net_pnl_paise,
    underlying_entries_today,
    update_trailing_stop,
)
from te.risk.killswitch import KillSwitchTrippedError, is_currently_throttled
from te.risk.killswitch import check as check_killswitch
from te.risk.limits import (
    LimitBreachError,
    RiskLimitsConfig,
    check_daily_loss_limit,
    check_max_concurrent_positions,
    check_max_drawdown,
    check_max_trades_per_day,
)
from te.risk.regime import DEFAULT_REGIME_THROTTLE_CONFIG, compose_size_multipliers
from te.risk.sizing import size_position
from te.strategy.context import StrategyContext
from te.strategy.registry import get as get_strategy


@dataclass(frozen=True)
class InstrumentConfig:
    """One instrument's exchange + lot size, for a `CycleConfig` trading
    more than one instrument at once. `lot_size` here is whatever the
    caller resolved it to be — `te.engine.scheduler`'s builder resolves it
    from the real synced `instruments` table (never a hand-typed literal),
    per this project's standing rule that sizing data comes only from that
    table (see `te.engine.scheduler`'s `NSE_UNDERLYINGS`/`BSE_UNDERLYINGS`
    docstring)."""

    symbol: str
    exchange: str
    lot_size: int


@dataclass(frozen=True)
class CycleConfig:
    mode: str
    strategy_name: str
    #: Single-instrument/single-exchange config, kept for backward
    #: compatibility (every test/call site before multi-instrument support
    #: existed constructs `CycleConfig` this way). `instrument_configs`
    #: below, when set, takes priority — see `_resolve_instruments`.
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
    #: `None` (the default) means "use `instruments` x `exchange`/`lot_size`
    #: for all of them" — today's single-exchange behaviour, unchanged. Set
    #: this to trade instruments across different exchanges/lot sizes in one
    #: cycle (e.g. NIFTY/BANKNIFTY on NFO with different lot sizes, SENSEX on
    #: BFO) — see the plan's "Dashboard<->engine wiring remediation", Tier 1.
    instrument_configs: tuple[InstrumentConfig, ...] | None = None
    #: Caps a single position's notional at this % of capital — see
    #: `te.risk.sizing.size_position`'s docstring. `Decimal(100)` (a no-op)
    #: by default so every pre-existing `CycleConfig` construction is
    #: unaffected.
    max_position_size_pct: Decimal = Decimal(100)
    #: Stop/target as a PERCENTAGE of the entry premium. When set, these take
    #: priority over the absolute `stop_distance`/`target_distance` above.
    #: Absolute distances are index-point-scaled leftovers from before option
    #: contracts were resolved — against a real premium they mean wildly
    #: different things at ₹30 vs ₹300 (a ₹15 target is 50% of one and 5% of
    #: the other). `None` keeps the absolute behaviour, so every pre-existing
    #: `CycleConfig` and test is unaffected.
    stop_pct: Decimal | None = None
    target_pct: Decimal | None = None
    #: Trailing distance as a PERCENTAGE of entry premium. Same index-point
    #: problem as stop/target, but worse in effect: found live on
    #: 2026-07-31, an absolute ₹3 trail against a ₹676 option premium is
    #: 0.44% — tighter than tick-to-tick noise — and closed 14 of 14 trades
    #: on `trailing_stop` at an average hold of 3.1 minutes, none of them
    #: anywhere near their real stop or target. Option premium is several
    #: times more volatile than the underlying in percentage terms, so an
    #: option trail belongs in the tens of percent, not fractions of one.
    trailing_pct: Decimal | None = None
    #: Max entries per underlying per session. The ORB literature converges
    #: on one or two (and on stopping for the day after two stop-outs) as a
    #: choppy-day over-trading guard. `2` follows that; set `1` for the
    #: strictest common variant.
    max_entries_per_underlying_per_day: int = 2


def _exit_levels(config: CycleConfig, entry_premium: Paise) -> tuple[Paise, Paise]:
    """Stop/target for one entry. Percentage-based when `stop_pct`/
    `target_pct` are configured, else the historic absolute distances.

    Percentages are the correct form once a real option premium is being
    traded: a fixed ₹15 target is 50% of a ₹30 premium and 5% of a ₹300 one,
    so an absolute distance silently changes the strategy as premiums move."""
    stop_distance = (
        Paise(int(Decimal(int(entry_premium)) * config.stop_pct / Decimal(100)))
        if config.stop_pct is not None
        else config.stop_distance
    )
    target_distance = (
        Paise(int(Decimal(int(entry_premium)) * config.target_pct / Decimal(100)))
        if config.target_pct is not None
        else config.target_distance
    )
    return Paise(entry_premium - stop_distance), Paise(entry_premium + target_distance)


def _trailing_distance(config: CycleConfig, entry_premium: Paise) -> Paise | None:
    """Trailing distance for one entry — percentage of entry premium when
    `trailing_pct` is set, else the historic absolute distance."""
    if config.trailing_pct is None:
        return config.trailing_distance
    return Paise(int(Decimal(int(entry_premium)) * config.trailing_pct / Decimal(100)))


def _resolve_instruments(config: CycleConfig) -> list[InstrumentConfig]:
    if config.instrument_configs is not None:
        return list(config.instrument_configs)
    return [InstrumentConfig(symbol=s, exchange=config.exchange, lot_size=config.lot_size) for s in config.instruments]


def unrealized_pnl_paise(
    session: Session, *, store: BarStore, cost_model: CostModel, as_of: dt.datetime
) -> Paise:
    """Sum of every open position's mark-to-market P&L (negative when
    underwater), via the same `mark_to_market_pnl` formula a real close
    uses. Feeds `te.risk.limits.check_daily_loss_limit` so the daily-loss
    halt sees unrealized losses too, not only realized ones — see that
    function's docstring for why the check itself can't compute this
    (pricing a mark needs a `BarStore`/`CostModel`, both above `te.risk` in
    the layer rule)."""
    total = 0
    for row in open_positions(session):
        current = Paise(
            latest_close_paise(store, row.symbol, as_of, fallback=row.entry_premium_paise)
        )
        total += int(
            mark_to_market_pnl(
                entry_premium=Paise(row.entry_premium_paise),
                current_premium=current,
                qty=row.lots * row.lot_size,
                exchange=row.exchange,
                cost_model=cost_model,
                on=as_of.date(),
            )
        )
    return Paise(total)


def _row_to_position(row: OpenPositionRow) -> OpenPosition:
    trailing_distance = Paise(row.trailing_distance_paise) if row.trailing_distance_paise is not None else None
    exit_plan = ExitPlan(
        stop=Paise(row.stop_paise),
        trailing_distance=trailing_distance,
        # Derived, not stored — see `trailing_activation_for`. `current_stop`
        # (the ratchet's actual state) IS persisted, so nothing about an
        # already-trailing position is lost by recomputing the threshold.
        trailing_activation=trailing_activation_for(Paise(row.entry_premium_paise), trailing_distance),
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
    contract_resolver: ContractResolver | None = None,
) -> int:
    """One fetch->analyze->risk->decide->act pass across every configured
    instrument. Returns the persisted `cycle_id`.

    `contract_resolver` turns the rule's `long_call`/`long_put` intent on an
    INDEX into a concrete option contract with a real premium (see
    `te.engine.contract.OptionContractResolver`). It is REQUIRED in
    production: without it the raw `instrument` symbol and the rule's
    index-level "premium" are traded directly, which is only ever correct
    when `instrument` is already an option symbol (as in this module's tests
    and in backtests replayed from `option_bhav`). Live, `instrument` is
    `"NIFTY"` and no such contract exists on NFO — that was the defect found
    on 2026-07-31. `None` therefore preserves the historic behaviour for
    those callers rather than silently changing them.

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

    # Real, measured per-stage wall-clock cost for this cycle — feeds the
    # dashboard's "Current cycle" strip (`PipelineStrip`/`get_dashboard_
    # snapshot`). `reached=False` for a stage means it genuinely did not run
    # this cycle (e.g. every instrument skipped before sizing, so
    # `decide`/`act` never fired) — never marked "done" just to fill the bar.
    stage_ms: dict[str, float] = dict.fromkeys(PIPELINE_STAGE_KEYS, 0.0)
    stage_reached: dict[str, bool] = dict.fromkeys(PIPELINE_STAGE_KEYS, False)

    def _persist_pipeline() -> None:
        with session_scope(session_factory) as session:
            set_last_cycle_pipeline(
                session,
                cycle_id=cycle_id,
                as_of=as_of,
                stages={
                    k: PipelineStageTiming(reached=stage_reached[k], elapsed_ms=round(stage_ms[k]))
                    for k in PIPELINE_STAGE_KEYS
                },
            )

    # Portfolio-level risk (daily loss incl. unrealized, and drawdown vs
    # peak equity) is checked ONCE per cycle here, unconditionally — not
    # nested inside the per-instrument loop below. It used to run only when
    # some instrument's rule fired a signal, so a real breach could go
    # undetected indefinitely on a day with zero signals; computing it once
    # also avoids re-deriving the same portfolio equity once per instrument
    # for an identical answer. `check_killswitch` is deliberately still
    # re-checked per instrument below too, since a same-cycle overfill halt
    # (triggered synchronously by an earlier instrument's fill) must still
    # block a later instrument in the same cycle.
    portfolio_blocked_reason: str | None = None
    throttled = False
    _t0 = time.perf_counter()
    with session_scope(session_factory) as session:
        try:
            check_killswitch(session)
            unrealized = unrealized_pnl_paise(session, store=store, cost_model=cost_model, as_of=as_of)
            equity = Paise(int(config.capital) + int(total_net_pnl_paise(session)) + int(unrealized))
            check_daily_loss_limit(
                session, config.risk_limits, on=as_of.date(), now=as_of, unrealized_pnl_paise=unrealized
            )
            check_max_drawdown(session, config.risk_limits, now=as_of, current_equity_paise=equity)
        except (KillSwitchTrippedError, LimitBreachError) as exc:
            # Recorded AFTER this session closes (via `_skip`, below) rather
            # than on this session, same convention as the per-instrument
            # gate further down — never nest one SQLite write transaction
            # inside another.
            portfolio_blocked_reason = str(exc)
        else:
            throttled = is_currently_throttled(session)
    stage_ms["risk"] += (time.perf_counter() - _t0) * 1000
    stage_reached["risk"] = True

    # No new entries at or after the hard exit time. `PaperCycleRunner.run_once`
    # runs `run_entry_cycle` and then `run_exit_cycle` in the SAME invocation,
    # and `evaluate_position` checks `now_ist >= hard_exit_by` before anything
    # else — so a position opened at or after that time is closed by the very
    # next statement, at the same premium, having held zero seconds of market
    # exposure. Gross P&L is exactly 0 and the round-trip cost (~Rs 55-65) is
    # pure loss, repeatable across every active underlying.
    if portfolio_blocked_reason is None:
        now_ist_time = as_of.astimezone(IST).timetz().replace(tzinfo=None)
        if now_ist_time >= config.hard_exit_by:
            portfolio_blocked_reason = (
                f"{now_ist_time:%H:%M} IST is at or past the hard exit time ({config.hard_exit_by:%H:%M}) — "
                f"a new entry would be force-closed this same cycle for a guaranteed round-trip cost"
            )

    if portfolio_blocked_reason is not None:
        for instrument_config in _resolve_instruments(config):
            _skip(instrument_config.symbol, portfolio_blocked_reason)
        _persist_pipeline()
        return cycle_id

    for instrument_config in _resolve_instruments(config):
        instrument = instrument_config.symbol
        exchange = instrument_config.exchange
        lot_size = instrument_config.lot_size

        strategy = get_strategy(config.strategy_name)
        _t0 = time.perf_counter()
        ctx = StrategyContext(store=store, instrument=instrument, exchange=exchange, as_of=as_of)
        _t1 = time.perf_counter()
        stage_ms["fetch"] += (_t1 - _t0) * 1000
        stage_reached["fetch"] = True

        evaluation = strategy.evaluate(ctx)
        stage_ms["analyze"] += (time.perf_counter() - _t1) * 1000
        stage_reached["analyze"] = True

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

        # Max entries per underlying per session — the standard ORB
        # "don't over-trade a choppy day" guard (the literature converges on
        # one or two, and on stopping after two stop-outs). This is a RISK
        # rule and is deliberately NOT the fix for re-entry churn: that was a
        # correctness bug in the rule itself (level- vs edge-triggered
        # breakout detection) and is fixed in `te.strategy.orb`. Checked here
        # rather than in the gate block below because it is per-strategy
        # discipline, not a portfolio/account limit.
        with session_scope(session_factory) as session:
            entries_today = underlying_entries_today(
                session, strategy=config.strategy_name, underlying=instrument, on=as_of.date()
            )
        if entries_today >= config.max_entries_per_underlying_per_day:
            _skip(
                instrument,
                f"{entries_today} entr(ies) on this underlying today, at or above the per-session limit of "
                f"{config.max_entries_per_underlying_per_day}",
            )
            continue

        # Per-instrument gates only — portfolio-level checks already ran
        # once above. These two depend on state that can change WITHIN this
        # loop (an earlier instrument in this same cycle opening a position
        # moves both counters), so they stay re-checked per instrument.
        # `check_killswitch` is repeated too: a same-cycle overfill halt
        # from an earlier instrument's synchronous fill must still block a
        # later one.
        _t2 = time.perf_counter()
        blocked_reason: str | None = None
        with session_scope(session_factory) as session:
            try:
                check_killswitch(session)
                check_max_concurrent_positions(session, config.risk_limits)
                check_max_trades_per_day(session, config.risk_limits, on=as_of.date())
            except (KillSwitchTrippedError, LimitBreachError) as exc:
                # Recorded AFTER this session closes (via `_skip`) rather
                # than on this session, so the skip write never nests one
                # SQLite write transaction inside another.
                blocked_reason = str(exc)
        if blocked_reason is not None:
            stage_ms["risk"] += (time.perf_counter() - _t2) * 1000
            _skip(instrument, blocked_reason)
            continue

        # Contract resolution — the index breakout becomes a real option to
        # buy. Everything downstream (sizing, cost model, stop/target, the
        # order itself, MTM) then operates on the OPTION's premium instead of
        # the index level. Counted under `fetch`: it is I/O to obtain the
        # tradeable instrument, not a decision.
        trade_symbol = instrument
        trade_exchange = exchange
        entry_premium = signal.entry_premium
        if contract_resolver is not None:
            _tc = time.perf_counter()
            contract = contract_resolver(instrument, signal.direction, as_of)
            stage_ms["fetch"] += (time.perf_counter() - _tc) * 1000
            if contract is None:
                _skip(instrument, "no tradeable option contract could be resolved (see logs for the guard that fired)")
                continue
            trade_symbol = contract.symbol
            trade_exchange = contract.exchange
            lot_size = contract.lot_size
            entry_premium = contract.premium

        stop_premium, target_premium = _exit_levels(config, entry_premium)

        sizing = size_position(
            capital=config.capital,
            risk_budget_pct=config.risk_budget_pct,
            premium=entry_premium,
            stop_premium=stop_premium,
            target_premium=target_premium,
            lot_size=lot_size,
            costs=cost_model,
            exchange=trade_exchange,
            on=as_of.date(),
            min_edge_multiple=config.min_edge_multiple,
            max_position_size_pct=config.max_position_size_pct,
        )
        stage_ms["risk"] += (time.perf_counter() - _t2) * 1000
        if sizing.lots == 0:
            _skip(instrument, sizing.rejected_reason or "sizing rejected with no reason (bug)")
            continue

        # `ml_hook` is the ONLY place `te.ml` can touch this decision — see
        # this function's docstring. Below `gating` stage this is always
        # `MLInfluence(1, False, ...)`, so `lots`/the decision to trade are
        # unchanged; this block is a structural no-op in shadow/advisory.
        _t3 = time.perf_counter()
        influence = MLInfluence(size_multiplier=Decimal(1), veto=False, displayed_verdict=None)
        if ml_hook is not None:
            influence = ml_hook.evaluate(instrument=instrument, as_of=as_of, cycle_id=cycle_id)

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
        stage_ms["decide"] += (time.perf_counter() - _t3) * 1000
        stage_reached["decide"] = True

        if influence.veto:
            _skip(instrument, "ML maturity gate vetoed this signal")
            continue
        if lots < 1:
            _skip(
                instrument,
                "throttle/ML size multiplier resized position below 1 lot "
                f"(combined_multiplier={combined_multiplier}, throttled={throttled}, "
                f"ml_multiplier={influence.size_multiplier})",
            )
            continue

        _t4 = time.perf_counter()
        request = OrderRequest(
            symbol=trade_symbol,
            exchange=trade_exchange,
            side="BUY",
            quantity=lot_size * lots,
            order_type="LIMIT",
            limit_price=entry_premium,
        )
        client_order_id = execution.submit(request)

        trailing_distance = _trailing_distance(config, entry_premium)
        exit_plan = ExitPlan(
            stop=stop_premium,
            trailing_distance=trailing_distance,
            trailing_activation=trailing_activation_for(entry_premium, trailing_distance),
            target=target_premium,
            max_hold=config.max_hold,
            hard_exit_by=config.hard_exit_by,
        )
        with session_scope(session_factory) as session:
            insert_open_position(
                session,
                client_order_id=client_order_id,
                symbol=trade_symbol,
                exchange=trade_exchange,
                strategy=config.strategy_name,
                direction=signal.direction,
                lots=lots,
                lot_size=lot_size,
                entry_premium=entry_premium,
                exit_plan=exit_plan,
                opened_at=as_of,
            )
        stage_ms["act"] += (time.perf_counter() - _t4) * 1000
        stage_reached["act"] = True

    _persist_pipeline()
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

            _close_position(
                session,
                execution=execution,
                cost_model=cost_model,
                row=row,
                exit_premium=decision.exit_premium,
                exit_reason=decision.reason,
                as_of=as_of,
            )
            closed.append(row.client_order_id)

    return closed


def _close_position(
    session: Session,
    *,
    execution: ExecutionManager,
    cost_model: CostModel,
    row: OpenPositionRow,
    exit_premium: Paise,
    exit_reason: str,
    as_of: dt.datetime,
) -> None:
    """The shared close path for both a fired exit condition
    (`run_exit_cycle`) and a manual square-off (`square_off_position`) —
    same broker submission, same net-of-cost trade record, same
    `mark_position_closed`, so the two can never quietly diverge on how a
    position actually gets closed. Commits internally (matches
    `run_exit_cycle`'s pre-existing commit-around-the-broker-call
    ordering) — callers should not wrap this in their own transaction."""
    qty = row.lots * row.lot_size
    request = OrderRequest(
        symbol=row.symbol,
        exchange=row.exchange,
        side="SELL",
        quantity=qty,
        order_type="LIMIT",
        limit_price=exit_premium,
        reduce_only=True,
    )
    # Flush+commit anything pending before the broker round-trip:
    # `execution.submit` opens its own session, and holding this one's write
    # transaction open across that call would have one SQLite writer waiting
    # on another.
    session.commit()
    execution.submit(request)

    entry_premium = Paise(row.entry_premium_paise)
    costs = cost_model.round_trip(
        entry_premium=entry_premium,
        exit_premium=exit_premium,
        qty=qty,
        exchange=row.exchange,
        on=as_of.date(),
    )
    gross = GrossPnl(Paise((exit_premium - entry_premium) * qty))
    net = net_pnl(entry_premium, exit_premium, qty, costs)

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
        exit_premium=exit_premium,
        gross_pnl=Paise(gross),
        costs=costs.total,
        net_pnl=Paise(net),
        exit_reason=exit_reason,
        opened_at=_as_utc(row.opened_at),
        closed_at=as_of,
        mode="paper",
        # The levels the position was OPENED with — `row.stop_paise`, never
        # the trailed `row.current_stop_paise`. `row` is about to become a
        # closed `open_positions` row, so this is the last point at which
        # they can be carried onto the trade record.
        stop_paise=Paise(row.stop_paise),
        target_paise=Paise(row.target_paise),
    )
    session.commit()


def square_off_position(
    *,
    session_factory: sessionmaker[Session],
    execution: ExecutionManager,
    cost_model: CostModel,
    symbol: str,
    exchange: str,
    current_premium: Paise,
    as_of: dt.datetime,
) -> str | None:
    """Manual square-off — closes ONE open position immediately at
    `current_premium`, regardless of whether any stop/target/trailing/time
    condition has fired (unlike `run_exit_cycle`, which only closes on a
    real `evaluate_position` decision). Returns the closed position's
    `client_order_id`, or `None` if no open position matches
    `(symbol, exchange)`. Reuses `_close_position` — the same broker
    submission and net-of-cost trade record as an automatic exit, tagged
    `exit_reason="manual"`."""
    with session_scope(session_factory) as session:
        row = find_open_position(session, symbol=symbol, exchange=exchange)
        if row is None:
            return None
        _close_position(
            session,
            execution=execution,
            cost_model=cost_model,
            row=row,
            exit_premium=current_premium,
            exit_reason="manual",
            as_of=as_of,
        )
        return row.client_order_id
