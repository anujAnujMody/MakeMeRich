"""Backtests CREDIT SPREADS — sell one strike, buy a further one as
protection — on real option premiums for both legs.

### Why this exists

Every strategy tested so far BUYS a single option, and none beat a coin
flip: buying pays a fixed toll (spread, brokerage, decay) on every trade
that swamped anything the entry rules contributed. Selling flips that toll
to work FOR the position instead of against it — the reason retail traders
report it as the more survivable side of options.

The blocker has never been whether selling works; it is that naked
selling needs 1-2 lakh margin per position against 20-30k capital. A
CREDIT SPREAD — sell one strike, buy a further one — caps both the risk
and the margin (`(width - credit) x lot size`, roughly 15-35k), which is
what makes it worth testing at this capital level at all.

### What is measured, and how it reuses the existing signals

The 32 directional strategies in `te.strategy.rules` still decide WHEN and
WHICH WAY. A `long_call` signal (bullish) becomes a BULL PUT SPREAD: sell a
put some strikes out of the money, buy a further put as protection — it
profits if the index stays above the short strike, same directional bet the
single-leg version made, wrapped differently. A `long_put` signal becomes
the mirror BEAR CALL SPREAD.

Nothing about entry timing is reimplemented — `run_spread_backtest` drives
the same registered `Strategy` objects through the same `PrebuiltSession`
fast path as `te.backtest.strategy_lab.run_many`. Only what happens AFTER
the signal fires is different: two real contracts instead of one, and a
combo mark-to-market instead of a single premium path.

### The two approximations here, stated rather than hidden

1. **Mark-to-market uses bar CLOSES, not highs/lows.** A single option's
   barrier walk can use intrabar high/low because both barriers live on the
   same instrument's own path. A spread's value is the DIFFERENCE of two
   separate option series, and their highs/lows do not necessarily occur in
   the same minute — combining them would invent a combo price that never
   traded. Using closes at each shared minute is the honest, slightly more
   conservative substitute.
2. **All four legs (open x2, close x2) are costed even for a time exit that
   never triggers stop or target.** A spread is never partially closed here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import pandas as pd

from te.backtest.scoring import ScoredSample, score_many, score_r_multiples
from te.backtest.strategy_lab import (
    LAST_ENTRY,
    MAX_ENTRIES_PER_DAY,
    _DaySlice,
    _derive_session,
    _sessions,
    _WholeSymbolCache,
)
from te.data.asof import bars_asof
from te.data.barstore import BarStore
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.domain.symbols import ParsedOptionSymbol
from te.ml.trials import TrialLedger
from te.strategy.context import StrategyContext
from te.strategy.registry import get as get_strategy
from te.strategy.session_rule import _PREBUILT_KEY

#: `TrialLedger` scope. DELIBERATELY SEPARATE from
#: `te.backtest.strategy_lab.TRIAL_SCOPE`: a credit spread is a different
#: instrument (defined-risk, decay working for the position) from a bought
#: option, and pooling the two scopes would let a spread's score be
#: deflated by how many BUYING strategies were tried — a search in one
#: instrument does not make a discovery in a different one less surprising.
TRIAL_SCOPE = "spread_backtest"


@dataclass(frozen=True)
class SpreadGeometry:
    #: Strikes the SHORT leg sits from at-the-money.
    short_otm: int
    #: Additional strikes the LONG (protective) leg sits beyond the short
    #: leg. The width of the spread, in strikes.
    width_strikes: int
    #: Close early once this fraction of the credit received is captured.
    #: 0.5 (close at 50% of max profit) is the figure repeatedly cited by
    #: professional credit-spread practice — locking in the well-understood
    #: part of the return rather than holding for a shrinking last half
    #: while gamma risk rises near expiry.
    profit_target_pct: Decimal
    #: Stop once the cost to close reaches this multiple of the credit
    #: received (1.0 = give back the whole credit; the common practitioner
    #: figure is 2x credit debit to close, i.e. a loss equal to the credit).
    stop_loss_multiple: Decimal
    max_hold: dt.timedelta
    #: Days-to-expiry band the trade must fall inside, INCLUSIVE.
    #:
    #: Uncontrolled until 2026-08-02, and NOT ACTUALLY CONTROLLED until
    #: 2026-08-05 — the first attempt applied this bound after calling
    #: `nearest`, which stops at the first expiry inside the upper bound,
    #: so a later band mostly selected NOTHING rather than a later expiry.
    #: Banded results produced between those dates are drawn from the
    #: minority of days with no nearer expiry listed and should not be
    #: trusted. The original confound was real either way: `nearest()`
    #: simply took whatever weekly expiry was within 7 days, so a single
    #: reported result averaged eight different instruments.
    #:
    #: Measured over 574 sessions, entries landed at 0 days
    #: (21%), 1 day (19%), 2 (15%), 3 (14%), 4 (6%), 5 (6%) and 6 (18%).
    #: A credit spread expiring today and one expiring in six days have
    #: almost nothing in common — decay, gamma and breach probability all
    #: differ by an order of magnitude — so pooling them produced an average
    #: that described no tradeable instrument at all.
    min_days_to_expiry: int = 0
    max_days_to_expiry: int = 7

    def __str__(self) -> str:
        hold_min = int(self.max_hold.total_seconds() // 60)
        return (
            f"otm{self.short_otm}/w{self.width_strikes}/dte{self.min_days_to_expiry}-{self.max_days_to_expiry}"
            f"/tp{self.profit_target_pct}/sl{self.stop_loss_multiple}/{hold_min}m"
        )


@dataclass(frozen=True)
class SpreadTrade:
    entry_ts: dt.datetime
    direction: str
    short_symbol: str
    long_symbol: str
    #: Credit received per unit, in paise — before costs.
    entry_credit: Paise
    exit_reason: str
    #: Net P&L per unit (i.e. before x lot_size), paise, AFTER all four
    #: legs' real brokerage/STT/GST.
    net_pnl_per_unit: Paise
    #: Maximum possible loss per unit had the spread gone fully against the
    #: position — `(width - credit)`, floored at the credit itself. The risk
    #: unit `r_multiple` is measured against.
    max_loss_per_unit: Paise
    r_multiple: float
    #: The day's real lot size — needed to turn `max_loss_per_unit` into an
    #: actual capital figure. See `_score`'s `median_margin_paise`.
    lot_size: int


@dataclass(frozen=True)
class SpreadBacktestResult:
    strategy: str
    instrument: str
    trades: int
    unresolved: int
    win_rate: float
    mean_r: float
    sharpe: float
    t_stat: float
    deflated: float
    n_trials_at_scoring: int
    #: Real margin this geometry needs, so the result can be checked against
    #: actual capital rather than assumed. Median across trades — the width
    #: in points barely varies, but the credit (and so the margin) does.
    median_margin_paise: Paise
    first_day: dt.date | None
    last_day: dt.date | None

    @property
    def beats_luck(self) -> bool:
        return self.deflated > 0.95


def _resolve_legs(
    contracts: OptionContractIndex, *, on: dt.date, index_level: Decimal, direction: str, geometry: SpreadGeometry
) -> tuple[ParsedOptionSymbol, ParsedOptionSymbol] | None:
    """The (short, long) contracts for this signal, or `None` if the
    archive cannot support the requested width at this strike distance.

    `long_call` (bullish) -> BULL PUT SPREAD: sell a put, buy a further-out
    put for protection. `long_put` (bearish) -> BEAR CALL SPREAD: sell a
    call, buy a further-out call. Both are credit spreads; which side of the
    chain they sit on is what makes one bullish and the other bearish.
    """
    option_type: Literal["CE", "PE"] = "PE" if direction == "long_call" else "CE"
    short = contracts.nearest(
        on=on,
        index_level=index_level,
        option_type=option_type,
        strikes_out_of_the_money=geometry.short_otm,
        max_days_to_expiry=geometry.max_days_to_expiry,
        min_days_to_expiry=geometry.min_days_to_expiry,
    )
    long = contracts.nearest(
        on=on,
        index_level=index_level,
        option_type=option_type,
        strikes_out_of_the_money=geometry.short_otm + geometry.width_strikes,
        max_days_to_expiry=geometry.max_days_to_expiry,
        min_days_to_expiry=geometry.min_days_to_expiry,
    )
    if short is None or long is None:
        return None
    # Both legs must sit on the SAME expiry. A spread whose legs expire on
    # different days is not a spread — it is two unrelated positions, with
    # none of the risk-capping this whole structure depends on.
    if short.expiry != long.expiry:
        return None
    return short, long


def _premium_at(store: BarStore, symbol: str, at: dt.datetime) -> Paise | None:
    visible = bars_asof(store, symbol, at, dt.timedelta(minutes=10), interval="1m")
    if visible.empty:
        return None
    return Paise(int(round(float(visible.iloc[-1]["c"]) * 100)))


def _walk_spread(
    *,
    store: BarStore,
    short_symbol: str,
    long_symbol: str,
    entry_ts: dt.datetime,
    geometry: SpreadGeometry,
) -> tuple[Paise, Paise, Paise, str, dt.datetime, Paise, Paise] | None:
    """`(entry_credit, short_entry, long_entry, exit_reason, exit_ts,
    short_exit, long_exit)`, or `None` when either leg has no usable quote —
    a real property of thin far-OTM strikes, counted by the caller rather
    than guessed at.

    Takes no cost model on purpose. The target and stop are GROSS premium
    levels, exactly as they would be entered with a broker, and every charge
    is applied afterwards by `_score_trade` on all four legs. This previously
    accepted `cost_model`, `exchange` and `lot_size` and used none of them,
    which read as though the exit decision were cost-aware when it is not.
    """
    short_entry = _premium_at(store, short_symbol, entry_ts)
    long_entry = _premium_at(store, long_symbol, entry_ts)
    if short_entry is None or long_entry is None or short_entry <= 0 or long_entry <= 0:
        return None
    entry_credit = Paise(int(short_entry) - int(long_entry))
    if entry_credit <= 0:
        # A negative or zero credit means the requested width could not be
        # priced sensibly on this day (illiquid quote, crossed market) —
        # refused rather than labelled a guaranteed loser.
        return None

    # `round()` on the float ratio, matching the rounding style everywhere
    # else in this module (`_premium_at`) rather than `Decimal.
    # to_integral_value()`'s banker's rounding, which would silently differ
    # from `te.domain.costs`'s ROUND_HALF_UP convention on a threshold
    # comparison that decides whether a trade exits as a win or a loss.
    target_cost = Paise(round(float(entry_credit) * float(1 - geometry.profit_target_pct)))
    stop_cost = Paise(round(float(entry_credit) * float(geometry.stop_loss_multiple)))

    horizon_end = entry_ts + geometry.max_hold
    short_bars = bars_asof(store, short_symbol, horizon_end, geometry.max_hold + dt.timedelta(minutes=1), interval="1m")
    long_bars = bars_asof(store, long_symbol, horizon_end, geometry.max_hold + dt.timedelta(minutes=1), interval="1m")
    short_by_ts = {row["event_ts"]: float(row["c"]) for _, row in short_bars.iterrows()}
    long_by_ts = {row["event_ts"]: float(row["c"]) for _, row in long_bars.iterrows()}
    shared_minutes = sorted(t for t in short_by_ts if t in long_by_ts and t > pd.Timestamp(entry_ts))

    last_shared: pd.Timestamp | None = None
    for ts in shared_minutes:
        if ts.to_pydatetime() > horizon_end:
            break
        last_shared = ts
        short_now = Paise(int(round(short_by_ts[ts] * 100)))
        long_now = Paise(int(round(long_by_ts[ts] * 100)))
        cost_to_close = Paise(int(short_now) - int(long_now))
        if cost_to_close <= target_cost:
            return entry_credit, short_entry, long_entry, "target", ts.to_pydatetime(), short_now, long_now
        if cost_to_close >= stop_cost:
            return entry_credit, short_entry, long_entry, "stop", ts.to_pydatetime(), short_now, long_now

    # TIME EXIT AT THE LAST MINUTE BOTH LEGS ACTUALLY TRADED, not at the
    # wall-clock `horizon_end`.
    #
    # This previously did `_premium_at(store, symbol, horizon_end)`, which
    # only looks 10 minutes either side of that timestamp. `horizon_end` is
    # `entry + max_hold`, so with a 375-minute hold and a 10:00 entry it
    # lands at 16:15 — forty-five minutes after the 15:30 close, where no
    # bar exists. The lookup returned `None`, the whole trade returned
    # `None`, and it was counted as "unresolved" and DISCARDED.
    #
    # The trades that survived were therefore only those that hit the profit
    # target DURING the session; every loser fell through to the dead
    # timestamp and vanished. That is survivorship bias of the worst kind,
    # and it produced a 100% win rate with t=+48 — a result that looked
    # spectacular and meant nothing. It also silently affected every earlier
    # spread run at shorter holds, where late-session entries hit the same
    # dead lookup (~25% of trades dropped).
    #
    # Exiting at the last shared minute is both unbiased and genuinely
    # tradeable: a position can always be closed at the last price that
    # traded. `None` is now returned only when the legs never traded
    # together after entry at all.
    if last_shared is None:
        return None
    short_exit = Paise(int(round(short_by_ts[last_shared] * 100)))
    long_exit = Paise(int(round(long_by_ts[last_shared] * 100)))
    return entry_credit, short_entry, long_entry, "time", last_shared.to_pydatetime(), short_exit, long_exit


def _score_trade(
    *,
    entry_credit: Paise,
    short_entry: Paise,
    long_entry: Paise,
    short_exit: Paise,
    long_exit: Paise,
    short_strike: Decimal,
    long_strike: Decimal,
    lot_size: int,
    exchange: str,
    cost_model: CostModel,
    on: dt.date,
) -> tuple[Paise, Paise, float]:
    """`(net_pnl_per_unit, max_loss_per_unit, r_multiple)`.

    All four legs are costed: SELL to open the short, BUY to open the long,
    BUY to close the short, SELL to close the long.
    """
    total_cost = Paise(
        int(cost_model.leg(side="SELL", premium=short_entry, qty=lot_size, exchange=exchange, on=on).total)
        + int(cost_model.leg(side="BUY", premium=long_entry, qty=lot_size, exchange=exchange, on=on).total)
        + int(cost_model.leg(side="BUY", premium=short_exit, qty=lot_size, exchange=exchange, on=on).total)
        + int(cost_model.leg(side="SELL", premium=long_exit, qty=lot_size, exchange=exchange, on=on).total)
    )
    gross_pnl_total = (int(short_entry) - int(short_exit) + int(long_exit) - int(long_entry)) * lot_size
    net_pnl_total = gross_pnl_total - total_cost
    net_pnl_per_unit = Paise(round(net_pnl_total / lot_size))

    width_paise = int(abs(short_strike - long_strike) * 100)
    # Floored at the credit itself: a spread priced so wide that its
    # nominal max loss is below the credit received would be a data
    # artefact, not a real risk figure.
    max_loss_per_unit = Paise(max(width_paise - int(entry_credit), int(entry_credit)))

    r_multiple = net_pnl_per_unit / max_loss_per_unit if max_loss_per_unit > 0 else 0.0
    return net_pnl_per_unit, max_loss_per_unit, r_multiple


@dataclass(frozen=True)
class RawSignal:
    """One strategy firing, BEFORE any spread structure is applied.

    Deliberately geometry-free. A signal depends only on the strategy and
    the price history, so a structure sweep can evaluate the strategies ONCE
    and then re-label the same signals under every geometry — instead of
    re-running the (much more expensive) per-minute evaluation loop for each
    one. Sweeping ~20 geometries the naive way costs ~20 full passes; this
    costs one pass plus 20 cheap labelling runs.
    """

    entry_ts: dt.datetime
    direction: str
    index_level: Decimal
    day: dt.date
    lot_size: int


def collect_signals(
    *,
    strategy_names: list[str],
    store: BarStore,
    contracts: OptionContractIndex,
    lot_size_for: Callable[[dt.date], int],
    instrument: str = "NIFTY",
) -> dict[str, list[RawSignal]]:
    """Every firing each strategy produces over the whole history.

    One pass, shared across strategies — the same `PrebuiltSession` sharing
    `te.backtest.strategy_lab.run_many` uses, for the same reason.
    """
    sessions = _sessions(store, instrument)
    expiry_dates = frozenset(contracts.expiries)
    signals: dict[str, list[RawSignal]] = {name: [] for name in strategy_names}

    previous_session: pd.DataFrame | None = None
    for day, frame in sessions:
        day_store = _DaySlice(store.root, frame)
        first = dt.datetime.combine(day, dt.time(9, 16), tzinfo=IST)
        last = dt.datetime.combine(day, LAST_ENTRY, tzinfo=IST)
        ctx = StrategyContext(
            store=day_store, instrument=instrument, exchange="NSE_INDEX", as_of=first, expiry_dates=expiry_dates
        )
        prebuilt = _derive_session(frame, previous_session, expiry_dates=expiry_dates)
        previous_session = frame
        if prebuilt is None:
            continue
        ctx.state[_PREBUILT_KEY] = prebuilt

        strategies = {name: get_strategy(name) for name in strategy_names}
        entries_today = dict.fromkeys(strategy_names, 0)
        lot_size = lot_size_for(day)

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
                signals[name].append(
                    RawSignal(
                        entry_ts=evaluation.timestamp,
                        direction=signal.direction,
                        index_level=Decimal(int(signal.entry_premium)) / 100,
                        day=day,
                        lot_size=lot_size,
                    )
                )
            as_of += dt.timedelta(minutes=1)
    return signals


def label_signals(
    *,
    signals: list[RawSignal],
    store: BarStore,
    contracts: OptionContractIndex,
    cost_model: CostModel,
    geometry: SpreadGeometry,
    exchange: str = "NFO",
) -> tuple[list[SpreadTrade], int]:
    """`(trades, unresolved)` for one strategy's signals under `geometry`."""
    trades: list[SpreadTrade] = []
    unresolved = 0
    for raw in signals:
        on = raw.entry_ts.astimezone(IST).date()
        legs = _resolve_legs(
            contracts, on=on, index_level=raw.index_level, direction=raw.direction, geometry=geometry
        )
        if legs is None:
            unresolved += 1
            continue
        short_leg, long_leg = legs
        walked = _walk_spread(
            store=store, short_symbol=short_leg.symbol, long_symbol=long_leg.symbol,
            entry_ts=raw.entry_ts, geometry=geometry,
        )
        if walked is None:
            unresolved += 1
            continue
        entry_credit, short_entry, long_entry, reason, _exit_ts, short_exit, long_exit = walked
        net_pnl, max_loss, r_multiple = _score_trade(
            entry_credit=entry_credit, short_entry=short_entry, long_entry=long_entry,
            short_exit=short_exit, long_exit=long_exit, short_strike=short_leg.strike,
            long_strike=long_leg.strike, lot_size=raw.lot_size, exchange=exchange,
            cost_model=cost_model, on=on,
        )
        trades.append(
            SpreadTrade(
                entry_ts=raw.entry_ts, direction=raw.direction, short_symbol=short_leg.symbol,
                long_symbol=long_leg.symbol, entry_credit=entry_credit, exit_reason=reason,
                net_pnl_per_unit=net_pnl, max_loss_per_unit=max_loss, r_multiple=r_multiple,
                lot_size=raw.lot_size,
            )
        )
    return trades, unresolved


def run_spread_backtest(
    *,
    strategy_names: list[str],
    store: BarStore,
    contracts: OptionContractIndex,
    cost_model: CostModel,
    lot_size_for: Callable[[dt.date], int],
    geometry: SpreadGeometry,
    instrument: str = "NIFTY",
    exchange: str = "NFO",
    trial_ledger: TrialLedger | None = None,
    run_id: str = "manual",
    since: dt.date | None = None,
    until: dt.date | None = None,
) -> dict[str, SpreadBacktestResult]:
    """Backtests credit spreads for every strategy in `strategy_names` under
    ONE geometry. For many geometries, use `collect_signals` +
    `label_signals` directly so the strategy pass happens only once.

    `since`/`until` restrict which sessions COUNT, which is what makes a
    walk-forward split possible: tune on one period, then check the same
    geometry on a period that had no chance to influence it. Any geometry
    chosen after looking at results is in-sample by construction, and only
    an out-of-sample period can tell it apart from a lucky fit.
    """
    cached_store = _WholeSymbolCache(store.root, store)
    sessions = _sessions(store, instrument)
    signals = collect_signals(
        strategy_names=strategy_names, store=store, contracts=contracts,
        lot_size_for=lot_size_for, instrument=instrument,
    )
    labelled: dict[str, tuple[list[SpreadTrade], int]] = {}
    for name in strategy_names:
        window = [
            s for s in signals[name] if (since is None or s.day >= since) and (until is None or s.day <= until)
        ]
        labelled[name] = label_signals(
            signals=window, store=cached_store, contracts=contracts,
            cost_model=cost_model, geometry=geometry, exchange=exchange,
        )

    # ONE batch, ONE trial count — see `score_many`. Scoring inside the loop
    # above judged the last strategy against every earlier one's trial while
    # the first was judged against none of them.
    scored = score_many(
        {name: [t.r_multiple for t in labelled[name][0]] for name in strategy_names},
        scope=TRIAL_SCOPE,
        config_hash_for={name: f"{name}:{instrument}" for name in strategy_names},
        trial_ledger=trial_ledger,
        run_id=run_id,
    )
    return {
        name: _assemble(
            strategy=name, instrument=instrument, scored=scored[name], trades=labelled[name][0],
            unresolved=labelled[name][1],
            first_day=sessions[0][0] if sessions else None, last_day=sessions[-1][0] if sessions else None,
        )
        for name in strategy_names
    }


def _score(
    *,
    strategy: str,
    instrument: str,
    trades: list[SpreadTrade],
    unresolved: int,
    first_day: dt.date | None,
    last_day: dt.date | None,
    trial_ledger: TrialLedger | None,
    run_id: str,
) -> SpreadBacktestResult:
    """Scores ONE strategy. `run_spread_backtest` uses `score_many` instead,
    so a whole batch shares one trial count; this remains for callers
    measuring a single strategy on its own.

    `TRIAL_SCOPE` differs from the single-leg lab's on purpose — see this
    module's docstring — but the arithmetic and every guard around it are
    deliberately the SAME code, not a copy of it."""
    return _assemble(
        strategy=strategy,
        instrument=instrument,
        scored=score_r_multiples(
            [t.r_multiple for t in trades],
            scope=TRIAL_SCOPE,
            config_hash=f"{strategy}:{instrument}",
            trial_ledger=trial_ledger,
            run_id=run_id,
        ),
        trades=trades,
        unresolved=unresolved,
        first_day=first_day,
        last_day=last_day,
    )


def _assemble(
    *,
    strategy: str,
    instrument: str,
    scored: ScoredSample | None,
    trades: list[SpreadTrade],
    unresolved: int,
    first_day: dt.date | None,
    last_day: dt.date | None,
) -> SpreadBacktestResult:
    if scored is None:
        return SpreadBacktestResult(
            strategy=strategy, instrument=instrument, trades=len(trades), unresolved=unresolved, win_rate=0.0,
            mean_r=0.0, sharpe=0.0, t_stat=0.0, deflated=0.0, n_trials_at_scoring=0, median_margin_paise=Paise(0),
            first_day=first_day, last_day=last_day,
        )

    # PER LOT, not per unit — `max_loss_per_unit` x the day's real lot size.
    # Found on 2026-08-01: the first run reported "margin/lot: Rs 116",
    # sixty-five times too small, because this multiplication was missing —
    # `max_loss_per_unit` was displayed to the user as if it were already a
    # per-lot capital figure. It is exactly the number a reader would use to
    # decide whether a spread fits their account, so a silent 65x error
    # here is the single most consequential bug this module could have.
    margins = sorted(int(t.max_loss_per_unit) * t.lot_size for t in trades)
    median_margin = Paise(margins[len(margins) // 2])

    return SpreadBacktestResult(
        strategy=strategy, instrument=instrument, trades=scored.n, unresolved=unresolved, win_rate=scored.win_rate,
        mean_r=scored.mean_r, sharpe=scored.sharpe, t_stat=scored.t_stat, deflated=scored.deflated,
        n_trials_at_scoring=scored.n_trials_at_scoring,
        median_margin_paise=median_margin, first_day=first_day, last_day=last_day,
    )
