"""Sweeps the exit/sizing parameters nobody ever tested, scored in RUPEES.

Stop 20%, target 20%, risk 1.5%, at-the-money strike — every one of those was
chosen by a person and then never questioned. This module tests them.

### Why this exists rather than a loop over `run_many`

The obvious implementation is to call `run_many` once per combination. The grid
is 4 risk levels x 4 stops x 3 targets x 3 strike offsets = 144, and a single
walk over 636 sessions x 375 minutes costs roughly five minutes, so the obvious
implementation costs about twelve hours.

It is also almost entirely wasted work, because **a strategy's firings do not
depend on any swept parameter**. `strategy.evaluate(ctx)` sees bars and nothing
else: it has no idea where the stop will sit, which strike will be bought, or
how much capital is at risk. Only the LABELLING (which barrier is hit) and the
SIZING (how many lots are affordable) depend on the grid.

So the work splits three ways, cheapest last:

1. `collect_firings` walks history ONCE per strategy. Expensive, shared by all
   144 combinations.
2. Labelling runs once per (stop, target, strike) — 36 times, not 144 — because
   risk-per-trade cannot change which barrier a premium path touches.
3. Re-sizing runs per risk level on top of each labelled set, from premiums
   already carried on `StrategyTrade`. No bar reads at all.

### One deliberate difference from `run_many`

`run_many` stops EVALUATING a strategy for the rest of a day once its daily
loss limit or consecutive-loss standdown trips. This module always collects the
firing and declines the trade in the replay instead.

That is both more correct and necessary. More correct because the strategy did
fire — we simply would not have taken it, and a signal we declined is not a
signal that never happened. Necessary because it makes the firing set identical
across all 144 combinations, which is the entire basis for walking history once.

The two can therefore differ slightly on days where a risk gate trips. Neither
is wrong; they answer marginally different questions, and this one is the
question a sweep is asking.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from te.backtest.daily import DailyReport, build_daily_report
from te.backtest.strategy_lab import (
    LAST_ENTRY,
    MAX_ENTRIES_PER_DAY,
    StrategyTrade,
    _DaySlice,
    _derive_session,
    _label,
    _sessions,
    _WholeSymbolCache,
)
from te.data.barstore import BarStore
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.risk.limits import consecutive_losses_breached, daily_loss_breached, drawdown_breached
from te.risk.sizing import size_position
from te.strategy.context import StrategyContext
from te.strategy.registry import get as get_strategy
from te.strategy.session_rule import _PREBUILT_KEY


@dataclass(frozen=True)
class Firing:
    """One signal, before any parameter has been applied to it.

    Deliberately carries nothing about stops, targets, strikes or size — that
    is what lets a single walk serve the whole grid.
    """

    entry_ts: dt.datetime
    direction: str
    #: The underlying's level at the firing, used to resolve a strike.
    index_level: Decimal


@dataclass(frozen=True)
class SweepCell:
    """One (strategy, risk, stop, target, strike) result."""

    strategy: str
    risk_pct: Decimal
    stop_pct: Decimal
    target_pct: Decimal
    strikes_out_of_the_money: int
    report: DailyReport
    trades: int
    #: Labelled outcomes `size_position` could not afford at this risk level.
    #: On Rs 30,000 this is usually most of them, and hiding it would make a
    #: strategy look far more active than it was.
    unaffordable: int
    halted_days: int
    standdown_days: int
    drawdown_halted: bool
    final_equity_paise: int

    @property
    def mean_daily_paise(self) -> int:
        return self.report.mean_daily_paise


def collect_firings(
    *,
    strategy_names: Sequence[str],
    store: BarStore,
    contracts: OptionContractIndex,
    instrument: str = "NIFTY",
) -> tuple[dict[str, list[Firing]], list[dt.date]]:
    """Walks history ONCE, returning every firing per strategy.

    Mirrors `run_many`'s walk exactly — same `_DaySlice`, same prebuilt
    session frame, same `MAX_ENTRIES_PER_DAY` cap, same point-in-time reads
    through `bars_asof` — but applies no barrier, no sizing and no risk gate,
    because none of those can change what a strategy sees.

    Also returns the session dates, so callers can report the real range
    rather than assuming it.
    """
    sessions = _sessions(store, instrument)
    expiry_dates = frozenset(contracts.expiries)
    firings: dict[str, list[Firing]] = {name: [] for name in strategy_names}

    previous_session: pd.DataFrame | None = None
    for day, frame in sessions:
        day_store = _DaySlice(store.root, frame)
        first = dt.datetime.combine(day, dt.time(9, 16), tzinfo=IST)
        last = dt.datetime.combine(day, LAST_ENTRY, tzinfo=IST)
        ctx = StrategyContext(
            store=day_store,
            instrument=instrument,
            exchange="NSE_INDEX",
            as_of=first,
            expiry_dates=expiry_dates,
        )
        prebuilt = _derive_session(frame, previous_session, expiry_dates=expiry_dates)
        previous_session = frame
        if prebuilt is None:
            continue
        ctx.state[_PREBUILT_KEY] = prebuilt

        strategies = {name: get_strategy(name) for name in strategy_names}
        entries_today = dict.fromkeys(strategy_names, 0)

        as_of = first
        # `<`, not `<=` -- see `te.backtest.strategy_lab.run_many`'s identical
        # fix for why the boundary minute itself must be excluded.
        while as_of < last:
            ctx.as_of = as_of
            for name, strategy in strategies.items():
                if entries_today[name] >= MAX_ENTRIES_PER_DAY:
                    continue
                evaluation = strategy.evaluate(ctx)
                if evaluation.verdict != "traded":
                    continue
                signal = getattr(strategy, "last_signal", None)
                if signal is None:
                    continue
                entries_today[name] += 1
                firings[name].append(
                    Firing(
                        entry_ts=evaluation.timestamp,
                        direction=signal.direction,
                        index_level=Decimal(int(signal.entry_premium)) / 100,
                    )
                )
            as_of += dt.timedelta(minutes=1)

    return firings, [day for day, _ in sessions]


def label_firings(
    firings: Sequence[Firing],
    *,
    store: BarStore,
    contracts: OptionContractIndex,
    cost_model: CostModel,
    lot_size_for: Callable[[dt.date], int],
    exchange: str,
    stop_pct: Decimal,
    target_pct: Decimal,
    strikes_out_of_the_money: int,
    max_hold: dt.timedelta,
) -> tuple[list[StrategyTrade], int]:
    """Labels firings at ONE (stop, target, strike). Returns
    `(trades, unlabelled)`.

    Sizing is deliberately left at `_label`'s unlimited defaults here, so
    nothing is rejected for affordability at this stage — affordability is a
    property of the risk level, which `replay` applies afterwards. Rejecting
    here would bake one risk budget into a labelled set meant to serve four.
    """
    trades: list[StrategyTrade] = []
    unlabelled = 0
    for firing in firings:
        trade, _ = _label(
            store=store,
            contracts=contracts,
            cost_model=cost_model,
            lot_size_for=lot_size_for,
            index_level=firing.index_level,
            entry_ts=firing.entry_ts,
            direction=firing.direction,
            exchange=exchange,
            stop_pct=stop_pct,
            target_pct=target_pct,
            max_hold=max_hold,
            strikes_out_of_the_money=strikes_out_of_the_money,
        )
        if trade is None:
            unlabelled += 1
        else:
            trades.append(trade)
    return trades, unlabelled


@dataclass(frozen=True)
class ReplayResult:
    daily_net_paise: dict[dt.date, int]
    trades: int
    unaffordable: int
    halted_days: int
    standdown_days: int
    drawdown_halted: bool
    final_equity_paise: int


def replay(
    trades: Sequence[StrategyTrade],
    *,
    capital: Paise,
    risk_budget_pct: Decimal,
    max_position_size_pct: Decimal,
    min_edge_multiple: Decimal,
    cost_model: CostModel,
    exchange: str,
    max_daily_loss_paise: int | None,
    max_consecutive_losses: int | None,
    max_drawdown_pct: Decimal | None,
    compound_equity: bool,
) -> ReplayResult:
    """Re-sizes an already-labelled set at one risk level and walks it
    forward through the real risk limits.

    No bar reads: every premium `size_position` needs was carried onto the
    `StrategyTrade` at labelling time. This is what makes four risk levels
    cost roughly nothing on top of one labelling pass.

    The limits are the SAME predicates the live engine uses
    (`te.risk.limits`), not re-implementations — a sweep whose risk rules
    drifted from live would be measuring a system nobody trades.
    """
    # ENTRY and EXIT are separate events, processed in strict time order.
    #
    # The obvious loop — walk trades by entry time and credit each outcome
    # immediately — uses the future, and was how this was first written. A
    # trade entered at 10:00 and resolved at 12:00 had its P&L added to
    # equity at 10:00, so a second entry at 10:30 was sized off money the
    # first trade had not yet made, and the daily-loss limit was checked
    # against a result that had not happened. With up to three entries a day
    # that is a real edge over the live engine, which obviously cannot see
    # any of it. Caught in review 2026-08-05.
    #
    # `(ts, kind, index)`: kind 0 = entry, 1 = exit, so an exit and an entry
    # landing on the same minute settle the exit FIRST — the conservative
    # order, since it can only reduce the equity the entry is sized against.
    events: list[tuple[dt.datetime, int, int]] = []
    for index, trade in enumerate(trades):
        events.append((trade.entry_ts, 0, index))
        events.append((trade.exit_ts or trade.entry_ts, 1, index))
    events.sort()

    equity = int(capital)
    peak = int(capital)
    daily: dict[dt.date, int] = {}
    lots_for: dict[int, int] = {}
    taken = 0
    unaffordable = 0
    halted_days = 0
    standdown_days = 0
    drawdown_halted = False

    current_day: dt.date | None = None
    day_net = 0
    recent_nets: list[int] = []
    day_loss_halted = False
    day_standdown = False

    for ts, kind, index in events:
        trade = trades[index]
        day = ts.astimezone(IST).date()
        if day != current_day:
            current_day, day_net, recent_nets = day, 0, []
            day_loss_halted = day_standdown = False

        if kind == 0:
            if drawdown_halted or day_loss_halted or day_standdown:
                continue
            sizing = size_position(
                capital=Paise(equity) if compound_equity else capital,
                risk_budget_pct=risk_budget_pct,
                premium=Paise(trade.entry_premium_paise),
                stop_premium=Paise(trade.stop_premium_paise),
                target_premium=Paise(trade.target_premium_paise),
                lot_size=trade.lot_size,
                costs=cost_model,
                exchange=exchange,
                on=trade.entry_ts.date(),
                min_edge_multiple=min_edge_multiple,
                max_position_size_pct=max_position_size_pct,
            )
            if sizing.lots == 0:
                unaffordable += 1
                continue
            lots_for[index] = sizing.lots
            taken += 1
            continue

        # kind == 1: the trade actually resolved. Only now does its money
        # exist. A trade never entered (halted, or unaffordable) has no
        # entry in `lots_for` and settles to nothing.
        lots = lots_for.pop(index, 0)
        if lots == 0:
            continue

        net_total = trade.net_paise_per_unit * trade.lot_size * lots
        equity += net_total
        day_net += net_total
        daily[day] = daily.get(day, 0) + net_total
        recent_nets.insert(0, net_total)

        if (
            max_daily_loss_paise is not None
            and not day_loss_halted
            and daily_loss_breached(net_paise=day_net, max_daily_loss_paise=max_daily_loss_paise)
        ):
            day_loss_halted = True
            halted_days += 1
        if (
            max_consecutive_losses is not None
            and not day_standdown
            and consecutive_losses_breached(recent_trade_nets=recent_nets, limit=max_consecutive_losses)
        ):
            day_standdown = True
            standdown_days += 1

        peak = max(peak, equity)
        if equity <= 0:
            drawdown_halted = True  # account wiped out — nothing left to size against
        elif max_drawdown_pct is not None and drawdown_breached(
            current_equity_paise=equity, peak_equity_paise=peak, max_drawdown_pct=max_drawdown_pct
        ):
            drawdown_halted = True

    return ReplayResult(
        daily_net_paise=daily,
        trades=taken,
        unaffordable=unaffordable,
        halted_days=halted_days,
        standdown_days=standdown_days,
        drawdown_halted=drawdown_halted,
        final_equity_paise=equity,
    )


def sweep(
    *,
    strategy_names: Sequence[str],
    store: BarStore,
    contracts: OptionContractIndex,
    cost_model: CostModel,
    lot_size_for: Callable[[dt.date], int],
    risk_pcts: Sequence[Decimal],
    stop_pcts: Sequence[Decimal],
    target_pcts: Sequence[Decimal],
    strike_offsets: Sequence[int],
    capital: Paise,
    max_position_size_pct: Decimal,
    min_edge_multiple: Decimal,
    max_daily_loss_paise: int | None,
    max_consecutive_losses: int | None,
    max_drawdown_pct: Decimal | None,
    compound_equity: bool = True,
    instrument: str = "NIFTY",
    exchange: str = "NFO",
    max_hold: dt.timedelta = dt.timedelta(hours=3),
    progress: Callable[[str], None] | None = None,
) -> list[SweepCell]:
    """The whole grid. One walk, `len(stop)*len(target)*len(strike)` labelling
    passes, and a cheap re-size per risk level."""
    cached = _WholeSymbolCache(store.root, store)
    if progress:
        progress("walking history once to collect firings...")
    firings, _ = collect_firings(
        strategy_names=strategy_names, store=store, contracts=contracts, instrument=instrument
    )
    if progress:
        progress(", ".join(f"{name}: {len(f):,} firings" for name, f in firings.items()))

    cells: list[SweepCell] = []
    for stop_pct in stop_pcts:
        for target_pct in target_pcts:
            for offset in strike_offsets:
                for name in strategy_names:
                    if progress:
                        progress(f"labelling {name} stop={stop_pct} target={target_pct} otm={offset}")
                    labelled, _ = label_firings(
                        firings[name],
                        store=cached,
                        contracts=contracts,
                        cost_model=cost_model,
                        lot_size_for=lot_size_for,
                        exchange=exchange,
                        stop_pct=stop_pct,
                        target_pct=target_pct,
                        strikes_out_of_the_money=offset,
                        max_hold=max_hold,
                    )
                    for risk_pct in risk_pcts:
                        result = replay(
                            labelled,
                            capital=capital,
                            risk_budget_pct=risk_pct,
                            max_position_size_pct=max_position_size_pct,
                            min_edge_multiple=min_edge_multiple,
                            cost_model=cost_model,
                            exchange=exchange,
                            max_daily_loss_paise=max_daily_loss_paise,
                            max_consecutive_losses=max_consecutive_losses,
                            max_drawdown_pct=max_drawdown_pct,
                            compound_equity=compound_equity,
                        )
                        cells.append(
                            SweepCell(
                                strategy=name,
                                risk_pct=risk_pct,
                                stop_pct=stop_pct,
                                target_pct=target_pct,
                                strikes_out_of_the_money=offset,
                                report=build_daily_report(
                                    result.daily_net_paise, capital=capital, trade_count=result.trades
                                ),
                                trades=result.trades,
                                unaffordable=result.unaffordable,
                                halted_days=result.halted_days,
                                standdown_days=result.standdown_days,
                                drawdown_halted=result.drawdown_halted,
                                final_equity_paise=result.final_equity_paise,
                            )
                        )
    return cells


def plateau(cells: Sequence[SweepCell], *, strategy: str, parameter: str) -> list[tuple[object, int, int]]:
    """`(value, mean daily paise averaged over every other parameter, n)`.

    The single most important reading aid here, and the reason it is a
    first-class function rather than a footnote. Per `backtest-expert`: a
    single best cell out of 144 is what noise looks like, and the real signal
    is a RANGE of values that all behave similarly. If 1%, 2% and 3% risk all
    land in the same place, that is a finding; if only 2% works while 1.5%
    and 2.5% do not, that is noise wearing a costume.
    """
    mine = [c for c in cells if c.strategy == strategy]
    buckets: dict[object, list[int]] = {}
    for cell in mine:
        buckets.setdefault(getattr(cell, parameter), []).append(cell.mean_daily_paise)
    return sorted(
        ((value, sum(v) // len(v), len(v)) for value, v in buckets.items()),
        key=lambda row: str(row[0]),
    )
