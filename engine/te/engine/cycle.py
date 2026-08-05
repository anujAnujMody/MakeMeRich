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
import functools
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

import structlog
from sqlalchemy.orm import Session, sessionmaker

from te.data.asof import latest_close_paise
from te.data.barstore import BarStore
from te.domain.clock import IST
from te.domain.clock import assume_utc as _as_utc
from te.domain.costs import CostModel
from te.domain.geometry import ExitGeometry
from te.domain.money import Paise
from te.domain.orders import OrderRequest
from te.domain.pnl import GrossPnl, mark_to_market_pnl, net_pnl
from te.domain.signal import ExitPlan, Signal
from te.engine.contract import ContractResolver, OptionContractResolver, ResolvedContract
from te.engine.exits import (
    DEFAULT_MAX_MARK_JUMP_PCT,
    OpenPosition,
    evaluate_position,
    sanity_checked_mark,
    time_exit,
)
from te.engine.state import PIPELINE_STAGE_KEYS, PipelineStageTiming, set_last_cycle_pipeline
from te.execution.halt import is_halted, set_halt
from te.execution.manager import ExecutionManager
from te.ml.gates import MLHook, MLInfluence
from te.persistence.db import session_scope
from te.persistence.models import OpenPositionRow
from te.persistence.repos.paper_trading import (
    correct_evaluation_verdict,
    engage_profit_lock,
    find_open_position,
    insert_open_position,
    insert_trade,
    mark_position_closed,
    open_positions,
    record_cycle,
    record_evaluation,
    record_risk_event,
    record_skipped_signal,
    total_net_pnl_paise,
    underlying_entries_today,
    update_last_mark,
    update_pending_mark,
    update_trailing_stop,
)
from te.risk.killswitch import KillSwitchTrippedError, is_currently_throttled
from te.risk.killswitch import check as check_killswitch
from te.risk.limits import (
    LimitBreachError,
    RiskLimitsConfig,
    check_consecutive_losses,
    check_daily_loss_limit,
    check_max_concurrent_positions,
    check_max_drawdown,
    check_max_trades_per_day,
)
from te.risk.regime import DEFAULT_REGIME_THROTTLE_CONFIG, compose_size_multipliers
from te.risk.sizing import size_position
from te.strategy.context import StrategyContext
from te.strategy.registry import get as get_strategy

logger = structlog.get_logger(__name__)


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
    #: Where this position's exits sit — ONE representation, either
    #: percentages of premium or absolute distances, never both. See
    #: `te.domain.geometry`: carrying both forms at once is what let
    #: "trailing disabled" silently restore a Rs 3 absolute trail.
    exit_geometry: ExitGeometry
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
    #: Max entries per underlying per session. The ORB literature converges
    #: on one or two (and on stopping for the day after two stop-outs) as a
    #: choppy-day over-trading guard. `2` follows that; set `1` for the
    #: strictest common variant.
    max_entries_per_underlying_per_day: int = 2
    #: Minutes of runway a new entry must have before `hard_exit_by`. `0`
    #: blocks only the guaranteed-zero-exposure case (entering at or after
    #: the hard exit itself) and is the historic behaviour; a positive value
    #: also refuses entries too late to reach their target. Set from the
    #: measured time-to-target distribution, never guessed — see
    #: `Settings.paper_cycle_min_minutes_before_hard_exit`.
    min_minutes_before_hard_exit: int = 0


def _resolve_instruments(config: CycleConfig) -> list[InstrumentConfig]:
    if config.instrument_configs is not None:
        return list(config.instrument_configs)
    return [InstrumentConfig(symbol=s, exchange=config.exchange, lot_size=config.lot_size) for s in config.instruments]


def unrealized_pnl_paise(
    session: Session,
    *,
    store: BarStore,
    cost_model: CostModel,
    as_of: dt.datetime,
    current_premium: Callable[[OpenPositionRow], Paise | None] | None = None,
) -> Paise:
    """Sum of every open position's mark-to-market P&L (negative when
    underwater), via the same `mark_to_market_pnl` formula a real close
    uses. Feeds `te.risk.limits.check_daily_loss_limit` so the daily-loss
    halt sees unrealized losses too, not only realized ones — see that
    function's docstring for why the check itself can't compute this
    (pricing a mark needs a `BarStore`/`CostModel`, both above `te.risk` in
    the layer rule).

    `current_premium` MUST be supplied on the live path. Without it this
    falls back to `store`, and an option contract has no recorded bars at
    all (only the four index spot symbols are subscribed) — so every mark
    resolved to the position's own entry premium and this function returned
    ~0 no matter how far underwater the book actually was. The daily-loss
    halt and the drawdown breaker were reading that zero and could not see
    an open loss of any size. The parameter is optional only so that
    backtest/test callers, which price from a real bar store, keep working
    unchanged."""
    total = 0
    for row in open_positions(session):
        marked: Paise | None = current_premium(row) if current_premium is not None else None
        if marked is not None:
            # READ-ONLY sanity check — see `sanity_checked_mark`'s docstring
            # and `te.engine.scheduler`'s "ONE premium source for both
            # cycles" comment, which states the invariant this closes: the
            # entry cycle (this function feeds its risk gates,
            # `check_daily_loss_limit`/`check_max_drawdown` below) runs
            # BEFORE the exit cycle, which is the sole owner of quarantining
            # a bad tick and persisting `pending_mark_paise`/
            # `last_mark_paise`. Found 2026-08-04 by a code-quality review of
            # the tick-sanity fix: it closed the hole for the exit decision
            # but left these risk gates reading the raw, unfiltered tick,
            # silently breaking the one-premium-source invariant — a single
            # bad tick could trip the daily-loss halt or mask a real
            # drawdown before the exit cycle ever saw (and quarantined) it.
            # The returned `new_pending` is discarded: this call must never
            # promote or persist a pending mark, only read the same
            # confirmed price the exit cycle's own decision will use.
            marked, _ = sanity_checked_mark(
                candidate=marked,
                last_confirmed=Paise(row.last_mark_paise) if row.last_mark_paise is not None else None,
                pending=Paise(row.pending_mark_paise) if row.pending_mark_paise is not None else None,
            )
        if marked is None and row.last_mark_paise is not None:
            marked = Paise(row.last_mark_paise)
        current = (
            marked
            if marked is not None
            else Paise(latest_close_paise(store, row.symbol, as_of, fallback=row.entry_premium_paise))
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
    exit_plan = ExitPlan(
        entry_premium=Paise(row.entry_premium_paise),
        stop=Paise(row.stop_paise),
        trailing_distance=Paise(row.trailing_distance_paise) if row.trailing_distance_paise is not None else None,
        target=Paise(row.target_paise),
        max_hold=dt.timedelta(seconds=row.max_hold_seconds),
        hard_exit_by=dt.time.fromisoformat(row.hard_exit_by),
        profit_lock_activation=(
            Paise(row.profit_lock_activation_paise) if row.profit_lock_activation_paise is not None else None
        ),
        profit_lock_buffer_pct=(
            Decimal(str(row.profit_lock_buffer_pct)) if row.profit_lock_buffer_pct is not None else None
        ),
    )
    return OpenPosition(
        symbol=row.symbol,
        exchange=row.exchange,
        strategy=row.strategy,
        direction=row.direction,  # type: ignore[arg-type]
        lot_size=row.lot_size,
        lots=row.lots,
        opened_at=_as_utc(row.opened_at),
        exit_plan=exit_plan,
        current_stop=Paise(row.current_stop_paise),
        profit_lock_engaged=row.profit_lock_engaged,
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
    current_premium: Callable[[OpenPositionRow], Paise | None] | None = None,
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

    def _skip(instrument: str, reason: str, *, evaluation_id: str | None = None) -> None:
        """Every early exit from the per-instrument loop below persists a
        `SkippedSignal` with the REAL reason — the plan's explicit
        call-out that a silently-swallowed skip is the exact bug class that
        meant the old engine never traded.

        `evaluation_id`: pass the current `evaluation.id` whenever this skip
        fires AFTER `record_evaluation` already persisted a "traded" verdict
        for it (every call site below the verdict check does). Corrects that
        row to "skipped" with the real reason — see
        `correct_evaluation_verdict`'s docstring for the 2026-08-04 incident
        this closes. Omitted only by the one call site that skips BEFORE any
        evaluation this cycle exists to correct."""
        with session_scope(session_factory) as session:
            record_skipped_signal(
                session, ts=as_of, strategy=config.strategy_name, instrument=instrument, reason=reason
            )
            if evaluation_id is not None:
                correct_evaluation_verdict(session, evaluation_id=evaluation_id, verdict="skipped", reason=reason)

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
    # What the account is ACTUALLY worth for sizing purposes. Falls back to
    # the configured capital only if the risk block below cannot run.
    sizing_equity = config.capital
    _t0 = time.perf_counter()
    with session_scope(session_factory) as session:
        try:
            check_killswitch(session)
            unrealized = unrealized_pnl_paise(
                session, store=store, cost_model=cost_model, as_of=as_of, current_premium=current_premium
            )
            realized = total_net_pnl_paise(session)
            # SIZING equity is realized-only, deliberately — while `equity`
            # below (for the drawdown breaker) also carries `unrealized`.
            #
            # The two want different things. The breaker asks "how far is
            # the account below its peak RIGHT NOW", which has to include
            # open positions or a large unrealized loss would be invisible
            # to it. Sizing asks "what can I actually stake on the next
            # trade", and marking that to an open position's minute-by-minute
            # paper profit would resize every new entry off a number that
            # has not settled and can reverse before it does.
            #
            # `max(0, ...)`: a wiped-out account must size to zero lots and
            # be REJECTED by `size_position` with a real reason, never wrap
            # into a negative budget.
            sizing_equity = Paise(max(0, int(config.capital) + int(realized)))
            equity = Paise(int(config.capital) + int(realized) + int(unrealized))
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

    # A new entry must have time left to actually work before the hard exit.
    #
    # At zero minutes this only blocks the degenerate case: `run_once` calls
    # `run_entry_cycle` then `run_exit_cycle` in the SAME invocation, and
    # `evaluate_position` checks `now_ist >= hard_exit_by` first, so a
    # position opened at or after that time is closed by the very next
    # statement at the same premium — zero market exposure, full round-trip
    # cost, repeatable across every active underlying.
    #
    # `min_minutes_before_hard_exit` widens that to the real problem, which
    # cost real money on 2026-07-31: a NIFTY position opened at 15:05 was
    # force-closed at 15:20 for -8.7% (-Rs 6,672, 82% of the day's loss).
    # Its stop never fired — the CLOCK closed it. Fifteen minutes is not
    # enough for a +40% option move to resolve, so such an entry takes the
    # full downside while its upside is unreachable by construction.
    if portfolio_blocked_reason is None:
        now_ist_time = as_of.astimezone(IST).timetz().replace(tzinfo=None)
        latest_entry = (
            dt.datetime.combine(dt.date.min, config.hard_exit_by)
            - dt.timedelta(minutes=config.min_minutes_before_hard_exit)
        ).time()
        if now_ist_time >= latest_entry:
            portfolio_blocked_reason = (
                f"{now_ist_time:%H:%M} IST leaves under {config.min_minutes_before_hard_exit}m before the "
                f"hard exit ({config.hard_exit_by:%H:%M}) — not enough time for the trade to reach its "
                f"target, so it would carry full downside against unreachable upside"
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
        # The broker's real expiry chain, when a resolver can supply it. A
        # calendar-gated rule (`expiry_day_only`) must not fall back to
        # guessing the weekday — see its docstring for the 83-of-125
        # mismatch that guess produced.
        expiry_dates = (
            contract_resolver.expiry_dates(instrument, as_of)
            if isinstance(contract_resolver, OptionContractResolver)
            else None
        )
        ctx = StrategyContext(
            store=store,
            instrument=instrument,
            exchange=exchange,
            as_of=as_of,
            expiry_dates=expiry_dates,
        )
        _t1 = time.perf_counter()
        stage_ms["fetch"] += (_t1 - _t0) * 1000
        stage_reached["fetch"] = True

        evaluation = strategy.evaluate(ctx)
        stage_ms["analyze"] += (time.perf_counter() - _t1) * 1000
        stage_reached["analyze"] = True

        with session_scope(session_factory) as session:
            record_evaluation(session, cycle_id=cycle_id, evaluation=evaluation)

        if evaluation.verdict != "traded":
            # No `evaluation_id` needed: `record_evaluation` just persisted
            # THIS verdict (already "skipped", never "traded"), so there is
            # nothing to correct — unlike every downstream skip below, all
            # of which fire after a "traded" verdict is already on disk.
            _skip(instrument, evaluation.reason)
            continue

        # Bound once per instrument, right after the "traded" verdict this
        # cycle is on disk, so every downstream skip below corrects it
        # without having to thread `evaluation_id` through by hand at each
        # call site. That hand-threading is exactly the failure mode this
        # guards against: a future eighth gate that forgets the keyword
        # would silently leave a persisted "traded" verdict uncorrected —
        # see `correct_evaluation_verdict`'s docstring for the 2026-08-04
        # incident that made this correction necessary in the first place.
        skip = functools.partial(_skip, evaluation_id=evaluation.id)

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
            skip(instrument, "strategy reported verdict=traded but produced no Signal")
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
            skip(
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
                # Also per-instrument rather than portfolio-level above: a
                # position opened earlier in THIS cycle can close and lose
                # before a later instrument is evaluated.
                check_consecutive_losses(session, config.risk_limits, on=as_of.date())
            except (KillSwitchTrippedError, LimitBreachError) as exc:
                # Recorded AFTER this session closes (via `_skip`) rather
                # than on this session, so the skip write never nests one
                # SQLite write transaction inside another.
                blocked_reason = str(exc)
        if blocked_reason is not None:
            stage_ms["risk"] += (time.perf_counter() - _t2) * 1000
            skip(instrument, blocked_reason)
            continue

        # Contract resolution — the index breakout becomes a real option to
        # buy. Everything downstream (sizing, cost model, stop/target, the
        # order itself, MTM) then operates on the OPTION's premium instead of
        # the index level. Counted under `fetch`: it is I/O to obtain the
        # tradeable instrument, not a decision.
        trade_symbol = instrument
        trade_exchange = exchange
        entry_premium = signal.entry_premium
        #: 0 = the broker reported no freeze quantity for this contract (or
        #: there is no contract resolver at all, i.e. the symbol is already
        #: an option). Nothing is capped in that case — see the guard below.
        freeze_qty = 0
        #: Pre-bound alongside the other per-instrument defaults above: the
        #: index-fallback path (no resolver) never assigns it, and the order
        #: built below reads it for the arrival bid/ask.
        contract: ResolvedContract | None = None
        if contract_resolver is not None:
            _tc = time.perf_counter()
            contract = contract_resolver(instrument, signal.direction, as_of)
            stage_ms["fetch"] += (time.perf_counter() - _tc) * 1000
            if contract is None:
                skip(
                    instrument,
                    "no tradeable option contract could be resolved (see logs for the guard that fired)",
                )
                continue
            trade_symbol = contract.symbol
            trade_exchange = contract.exchange
            lot_size = contract.lot_size
            # Buy at the ASK, not the LTP. This is a BUY order, so the ask is
            # the price actually payable; LTP is the last trade on either
            # side of the book. Using LTP booked the half-spread as free
            # profit on entry (and the exit mark does the same at the bid),
            # which on a 0.8%-spread contract silently overstated every
            # round trip. `ask` is already fetched for the spread guard and
            # is guaranteed non-zero by it.
            entry_premium = contract.ask
            freeze_qty = contract.freeze_qty

        levels = config.exit_geometry.levels(entry_premium)
        stop_premium, target_premium = levels.stop, levels.target

        sizing = size_position(
            # LIVE equity, not the static configured capital. Until
            # 2026-08-05 this passed `config.capital`, which meant the
            # engine sized every trade off the number a human last typed
            # into the dashboard and never off what the account was really
            # worth — while the drawdown breaker three hundred lines up was
            # already computing the true figure and using it.
            #
            # Both directions were wrong, and the losing one is the
            # dangerous one: after dropping from Rs 30,000 to Rs 25,000 the
            # engine kept risking 3% of THIRTY thousand, so the real risk
            # per trade silently grew from 3% to 3.6% exactly while the
            # account was shrinking. That is how a drawdown accelerates into
            # a wipe-out. Profits were mirror-imaged: they never raised
            # buying power, so the account could not compound.
            #
            # It also made every backtest in this repo optimistic about the
            # live engine rather than pessimistic: `run_many`/`sweep.replay`
            # take `compound_equity=True` and DO re-size off running equity,
            # so measured results assumed a discipline production did not
            # have.
            capital=sizing_equity,
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
            skip(
                instrument,
                sizing.rejected_reason or "sizing rejected with no reason (bug)",
            )
            continue

        # `ml_hook` is the ONLY place `te.ml` can touch this decision — see
        # this function's docstring. Below `gating` stage this is always
        # `MLInfluence(1, False, ...)`, so `lots`/the decision to trade are
        # unchanged; this block is a structural no-op in shadow/advisory.
        _t3 = time.perf_counter()
        influence = MLInfluence(size_multiplier=Decimal(1), veto=False, displayed_verdict=None)
        if ml_hook is not None:
            # NEVER allowed to raise into the trading loop. The whole premise
            # of the shadow stage is that the ML layer cannot affect a trade —
            # but an unguarded call breaks that in the one direction nobody
            # checks: `ShadowMLHook.evaluate` builds a feature row from bars
            # and a missing/short lookback raises, which would abort the
            # entry cycle for EVERY instrument, not just this one. A layer
            # that is structurally forbidden from changing a decision must
            # also be unable to prevent one. Same rule as
            # `ExecutionManager._observe_slippage`: measurement must not be
            # able to break the thing it measures.
            try:
                influence = ml_hook.evaluate(instrument=instrument, as_of=as_of, cycle_id=cycle_id)
            except Exception:  # noqa: BLE001 — see above; falls back to the inert influence
                logger.exception("ml hook failed; continuing with no ML influence", instrument=instrument)

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
            skip(instrument, "ML maturity gate vetoed this signal")
            continue
        if lots < 1:
            skip(
                instrument,
                "throttle/ML size multiplier resized position below 1 lot "
                f"(combined_multiplier={combined_multiplier}, throttled={throttled}, "
                f"ml_multiplier={influence.size_multiplier})",
            )
            continue

        # Exchange freeze quantity: the largest quantity permitted in ONE
        # order. Above it the exchange rejects outright — a live failure that
        # arrives as an opaque broker error, at the exact moment a position
        # was meant to open.
        #
        # Capped rather than chunked, deliberately. Chunking one signal into
        # several orders means several `client_order_id`s folding into one
        # position, which the execution store does not model, and there is
        # no live order adapter to test it against (`openalgo_rest.py` has no
        # order endpoints at all — `SimulatedBroker` is the only venue). A
        # capped order is correct and small; a chunking path validated
        # against nothing is neither.
        #
        # `freeze_qty` is the BROKER's per-contract value from
        # `optionsymbol`, never a hardcoded table: the published figures
        # disagree across sources (BANKNIFTY is quoted as both 600 and 900)
        # and they change. `0` means the broker did not report one, and
        # nothing is capped — inventing a limit is worse than not having it.
        if freeze_qty > 0 and lot_size * lots > freeze_qty:
            capped = freeze_qty // lot_size
            if capped < 1:
                skip(
                    instrument,
                    f"one lot ({lot_size}) exceeds the exchange freeze quantity ({freeze_qty}) for "
                    f"{trade_symbol} — this contract cannot be traded in any size",
                )
                continue
            with session_scope(session_factory) as session:
                record_risk_event(
                    session,
                    ts=as_of,
                    kind="freeze_qty_cap",
                    detail=(
                        f"{trade_symbol}: sized {lots} lot(s) = {lot_size * lots} qty, above the exchange "
                        f"freeze quantity {freeze_qty}; capped to {capped} lot(s)"
                    ),
                )
            lots = capped

        _t4 = time.perf_counter()
        request = OrderRequest(
            symbol=trade_symbol,
            exchange=trade_exchange,
            side="BUY",
            quantity=lot_size * lots,
            order_type="LIMIT",
            limit_price=entry_premium,
            # The real two-sided market this contract was resolved at, so
            # the fill can later be measured against where the market
            # actually was — not against our own limit, which would score a
            # genuine price move as bad execution. `contract` is None only
            # on the index-fallback path, which has no quote to record.
            arrival_bid=contract.bid if contract is not None else None,
            arrival_ask=contract.ask if contract is not None else None,
        )
        client_order_id = execution.submit(request)

        exit_plan = ExitPlan(
            entry_premium=entry_premium,
            stop=stop_premium,
            trailing_distance=levels.trailing_distance,
            target=target_premium,
            max_hold=config.max_hold,
            hard_exit_by=config.hard_exit_by,
            profit_lock_activation=levels.profit_lock_activation,
            profit_lock_buffer_pct=levels.profit_lock_buffer_pct,
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
    current_premium: Callable[[OpenPositionRow], Paise | None],
    as_of: dt.datetime,
    max_mark_jump_pct: float = DEFAULT_MAX_MARK_JUMP_PCT,
) -> list[str]:
    """Evaluates exits on every currently open position. `current_premium`
    supplies the live mark for one position's symbol (the caller's job to
    wire to a real quote — kept a callable here so this stays testable
    without a broker/quote feed), returning `None` when the position cannot
    be priced this cycle. Returns the `client_order_id`s of every position
    closed this cycle. Not gated by the kill switch — exiting a position is
    risk-REDUCING and should still be able to run during a halt; only new
    entries (`run_entry_cycle`) are blocked."""
    closed: list[str] = []
    # ONE session for the whole loop: the rows `open_positions()` returns are
    # already the live ORM objects to write through, so there is no need to
    # re-`session.get()` each one in a fresh session per position.
    with session_scope(session_factory) as session:
        rows = open_positions(session)
        for row in rows:
            position = _row_to_position(row)
            # One bad symbol must not abort exit management for every OTHER
            # open position: a persistent malformed quote envelope on one
            # contract would otherwise block stops and targets on all of them.
            try:
                premium = current_premium(row)
            except Exception:  # noqa: BLE001 — deliberately broad, see above
                logger.exception("pricing an open position raised; treating as unpriceable", symbol=row.symbol)
                premium = None

            if premium is not None:
                # A bad tick must not be allowed to fire a stop/target/trail
                # directly — see `sanity_checked_mark`'s docstring for the
                # 2026-08-04 incident (a single spurious tick fabricated a
                # "target hit" and closed two real positions). `accepted` is
                # either `premium` (trusted immediately or just confirmed by
                # a second agreeing reading) or the last confirmed price
                # (quarantined — nothing acts on `premium` this cycle).
                last_confirmed = Paise(row.last_mark_paise) if row.last_mark_paise is not None else None
                pending = Paise(row.pending_mark_paise) if row.pending_mark_paise is not None else None
                accepted, new_pending = sanity_checked_mark(
                    candidate=premium,
                    last_confirmed=last_confirmed,
                    pending=pending,
                    max_jump_pct=max_mark_jump_pct,
                )
                if new_pending is not None:
                    logger.warning(
                        "premium jump quarantined pending confirmation",
                        symbol=row.symbol,
                        candidate_paise=int(premium),
                        last_confirmed_paise=int(last_confirmed) if last_confirmed is not None else None,
                    )
                    record_risk_event(
                        session,
                        kind="mark_quarantined",
                        ts=as_of,
                        detail=(
                            f"{row.symbol}: candidate {int(premium)}p is more than {max_mark_jump_pct}% from "
                            f"the last confirmed "
                            f"{int(last_confirmed) if last_confirmed is not None else 'n/a'}p — held for "
                            "next-cycle confirmation, not acted on"
                        ),
                    )
                    update_pending_mark(session, row, new_pending)
                else:
                    update_last_mark(session, row, accepted, as_of)
                updated, decision = evaluate_position(position, current_premium=accepted, now=as_of)
            elif row.last_mark_paise is not None:
                # Stale mark: the clock-driven exits MUST still fire (a quote
                # outage cannot be allowed to strand a position past 15:20),
                # but a stop, target or trail ratchet off a stale price would
                # be acting on information we do not have.
                stale = Paise(row.last_mark_paise)
                logger.warning("marking position from a stale price", symbol=row.symbol, paise=int(stale))
                updated, decision = position, time_exit(position, now=as_of, exit_premium=stale)
            else:
                # Never priced since it opened, so not even a stale mark
                # exists. There is nothing honest to act on: acting on the
                # entry premium is what made stops undetectable in the first
                # place, and inventing an exit price here would book a
                # fabricated P&L into the trade record — the one thing this
                # engine must never do.
                #
                # So the price is still not fabricated. What changed on
                # 2026-08-04 is what happens NEXT. Previously this branch
                # just `continue`d, which skipped `time_exit` as well — so
                # the 15:15 hard exit and the max-hold never fired for an
                # unpriceable position and it stayed open indefinitely,
                # carrying real exposure, with only a log line to show for
                # it. An intraday engine silently holding a position
                # overnight is a worse failure than the pricing gap itself.
                #
                # Past the hard-exit time it therefore stops being a
                # data-quality note and becomes an operator emergency: HALT.
                # Note the hard exit still does NOT fire for this position —
                # there is no honest price to close it at. Escalating to a
                # human IS the resolution, not a step towards an automatic
                # one. The position stays open until someone squares it off
                # with the broker directly.
                # which blocks all new entries while leaving exits running,
                # and requires a human to clear. Squaring off automatically
                # is deliberately NOT done — in paper mode the simulated
                # broker needs a price to fill against, which is precisely
                # what we do not have, so an "automatic square-off" would
                # just be the fabricated price wearing a different hat.
                past_hard_exit = as_of.astimezone(IST).timetz().replace(tzinfo=None) >= position.exit_plan.hard_exit_by
                # Once, not once per cycle. Nothing here closes the position,
                # so without this guard every subsequent cycle re-halted and
                # wrote another risk event — an operator who cleared the halt
                # was re-halted seconds later, and `risk_events` grew a row
                # per cycle for as long as the row stayed open. Caught in
                # review 2026-08-05.
                if past_hard_exit and is_halted(session):
                    continue
                if past_hard_exit:
                    reason = (
                        f"{row.symbol}: still unpriceable at {position.exit_plan.hard_exit_by} — the hard exit "
                        f"cannot fire, so this position is open past its intended close with no way to "
                        f"value it. Square it off with the broker directly, then clear this halt."
                    )
                    logger.error("unpriceable position past its hard exit; halting", symbol=row.symbol)
                    record_risk_event(session, kind="position_unpriceable_past_hard_exit", ts=as_of, detail=reason)
                    # No `kind=DAILY_LOSS_HALT`: this must NOT clear itself
                    # overnight. It is exactly the case a human has to see.
                    set_halt(session, reason)
                else:
                    logger.error("open position has never been priced; exits cannot be evaluated", symbol=row.symbol)
                    record_risk_event(
                        session,
                        kind="position_unpriceable",
                        ts=as_of,
                        detail=f"{row.symbol}: no quote, no bar and no previous mark — exits not evaluated",
                    )
                continue

            if decision is None:
                # Only write when the stop actually moved (trailing ratchet
                # or the one-time profit lock) — an unchanged stop is the
                # common case every cycle.
                if int(updated.current_stop) != row.current_stop_paise:
                    update_trailing_stop(session, row, updated.current_stop)
                if updated.profit_lock_engaged and not row.profit_lock_engaged:
                    engage_profit_lock(session, row)
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
