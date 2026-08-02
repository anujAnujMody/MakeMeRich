"""Backtests any registered strategy on real option premiums, and scores the
result for how hard we looked.

### It drives the REAL strategy, not a copy

Every firing here comes from `registry.get(name).evaluate(ctx)` — the exact
object the live engine runs, through the same `StrategyContext` and the same
`bars_asof` point-in-time gate. Nothing about a rule is reimplemented for
backtesting, so a result cannot describe a strategy that differs from the one
that would trade. That is also what makes this the engine's own stress test:
running 32 strategies over 600 sessions exercises the live evaluation path
about 7 million times.

### The scoring is the point

A page listing 32 strategies with their raw backtest results is a machine for
manufacturing false winners. Bailey & Lopez de Prado's result is blunt: the
probability of selecting an overfit strategy grows rapidly with the number of
trials, and the best of N pure-noise trials looks excellent by construction.
We have already demonstrated this on ourselves twice — 85 rule/exit
combinations whose "best" was luck, and four open-interest features that
reversed sign on older data.

So `BacktestResult` carries a raw score AND a deflated one, every run is
appended to the `TrialLedger` (monotonic, no delete path), and the deflation
uses that ledger's honest count. Re-running a sweep therefore makes every
strategy look WORSE, which is the correct incentive: it prices in the extra
looking.

`deflated` is the only number that should ever reach a user-facing page.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd
import structlog

from te.backtest.replay import DEFAULT_LAST_ENTRY
from te.backtest.scoring import ScoredSample, score_many, score_r_multiples
from te.data.asof import bars_asof
from te.data.barstore import BAR_COLUMNS, BarStore
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.ml.labeling import label_one_firing_on_premium, premium_barrier_levels
from te.ml.trials import TrialLedger
from te.strategy.context import StrategyContext
from te.strategy.indicators import SESSION_OPEN_HOUR, SESSION_OPEN_MINUTE
from te.strategy.registry import get as get_strategy
from te.strategy.session_rule import _PREBUILT_KEY, PrebuiltSession

logger = structlog.get_logger(__name__)

#: `TrialLedger` scope every strategy backtest is filed under. One shared
#: scope on purpose: the multiple-testing correction must account for every
#: strategy we tried, not just re-runs of the one being looked at. Filing
#: each strategy separately would reset the count per strategy and defeat
#: the entire correction.
TRIAL_SCOPE = "strategy_backtest"

#: Entries stop here. IMPORTED, not re-declared: a firing the live engine
#: would refuse must not become evidence for or against a strategy, and
#: this constant has already drifted from the live rule once (it stayed at
#: 15:20 after the hard exit moved). `te.backtest.replay` owns it and is
#: pinned to `Settings` by `tests/backtest/test_replay.py`, so importing is
#: what makes every lab inherit that pin.
LAST_ENTRY = DEFAULT_LAST_ENTRY

#: Cap per strategy per day, mirroring the live entries cap. Without it one
#: trending session can contribute dozens of firings and the statistics
#: describe a handful of days rather than the rule.
MAX_ENTRIES_PER_DAY = 3


@dataclass(frozen=True)
class StrategyTrade:
    entry_ts: dt.datetime
    direction: str
    option_symbol: str
    barrier: str
    #: Outcome in units of the stop distance: -1.0 is a full stop, +1.0 is
    #: one stop distance earned, both NET of real brokerage/STT/GST.
    r_multiple: float


@dataclass(frozen=True)
class BacktestResult:
    strategy: str
    instrument: str
    trades: int
    unlabelled: int
    win_rate: float
    mean_r: float
    #: Per-trade Sharpe (mean/stdev of R). Deliberately NOT annualised —
    #: annualising needs a trade frequency assumption that differs per
    #: strategy, and comparing annualised figures across strategies that
    #: trade at different rates is how one gets flattered for free.
    sharpe: float
    t_stat: float
    #: `deflated_sharpe_ratio`: probability the result is real GIVEN how many
    #: strategies have ever been tried. Above 0.95 is the project's bar.
    deflated: float
    n_trials_at_scoring: int
    first_day: dt.date | None
    last_day: dt.date | None

    @property
    def beats_luck(self) -> bool:
        return self.deflated > 0.95


class _WholeSymbolCache(BarStore):
    """Read-through cache over entire symbol histories.

    A backtest reads the same option contract once per firing and the same
    index day 375 times; without this the run re-opens the same Parquet
    partitions millions of times. Read-only by construction — `append` is
    never called on this class.
    """

    def __init__(self, root: Path | str, inner: BarStore) -> None:
        super().__init__(root)
        self._inner = inner
        self._cache: dict[tuple[str, str], pd.DataFrame] = {}

    def read(
        self,
        symbol: str,
        start: dt.datetime,
        end: dt.datetime,
        interval: str,
        ingested_before: dt.datetime | None = None,
    ) -> pd.DataFrame:
        key = (symbol, interval)
        if key not in self._cache:
            whole = self._inner.read(
                symbol=symbol,
                start=dt.datetime(2000, 1, 1, tzinfo=dt.UTC),
                end=dt.datetime(2100, 1, 1, tzinfo=dt.UTC),
                interval=interval,
            )
            self._cache[key] = whole.sort_values("event_ts").reset_index(drop=True)
        df = self._cache[key]
        if df.empty:
            return pd.DataFrame(columns=BAR_COLUMNS)
        mask = (df["event_ts"] >= pd.Timestamp(start)) & (df["event_ts"] <= pd.Timestamp(end))
        if ingested_before is not None:
            mask &= df["ingested_at"] <= pd.Timestamp(ingested_before)
        return df.loc[mask].reset_index(drop=True)


def _derive_session(
    frame: pd.DataFrame, previous: pd.DataFrame | None, *, expiry_dates: frozenset[dt.date] | None = None
) -> PrebuiltSession | None:
    """One session's bars with the columns `SessionRule` guarantees.

    Deliberately mirrors `SessionRule._session_frame`'s derivation exactly
    for every column BOTH paths can compute; `tests/strategy/
    test_prebuilt_matches_live_path.py` asserts the two agree minute by
    minute when `expiry_dates` is omitted (its default), so a drift in a
    shared column fails the suite rather than silently making backtests
    disagree with live behaviour.

    `expiry_dates`, when given, adds an `is_expiry_day` column read from the
    REAL contract calendar (`OptionContractIndex.expiries`) rather than
    guessed from weekday. It is backtest-only and intentionally not part of
    that parity contract: the live engine has no such calendar wired yet
    (only a broker `expiry()` call would give one), so `ExpiryDayOnly` falls
    back to its weekday heuristic there — see that class's docstring for why
    trusting weekday alone was wrong for 83 of 125 real NIFTY expiries.
    """
    if frame.empty:
        return None
    session = frame.drop_duplicates(subset="event_ts", keep="last").sort_values("event_ts").reset_index(drop=True)
    session["ist"] = session["event_ts"].dt.tz_convert(IST)
    if previous is None or previous.empty:
        session["previous_close"] = float("nan")
        session["previous_high"] = float("nan")
        session["previous_low"] = float("nan")
    else:
        session["previous_close"] = float(previous["c"].iloc[-1])
        session["previous_high"] = float(previous["h"].max())
        session["previous_low"] = float(previous["l"].min())
    session_open = session["ist"].iloc[0].replace(
        hour=SESSION_OPEN_HOUR, minute=SESSION_OPEN_MINUTE, second=0, microsecond=0
    )
    session["minutes_from_open"] = (session["ist"] - session_open).dt.total_seconds() / 60
    if expiry_dates is not None:
        today = session["ist"].iloc[0].date()
        session["is_expiry_day"] = today in expiry_dates
    return PrebuiltSession(frame=session)


def _sessions(store: BarStore, symbol: str) -> list[tuple[dt.date, pd.DataFrame]]:
    frame = store.read(
        symbol=symbol,
        start=dt.datetime(2000, 1, 1, tzinfo=dt.UTC),
        end=dt.datetime(2100, 1, 1, tzinfo=dt.UTC),
        interval="1m",
    )
    if frame.empty:
        return []
    frame = frame.drop_duplicates(subset="event_ts", keep="last").sort_values("event_ts").reset_index(drop=True)
    local_dates = frame["event_ts"].dt.tz_convert(IST).dt.date
    return list(frame.groupby(local_dates, sort=True))


def run_many(
    *,
    strategy_names: list[str],
    store: BarStore,
    contracts: OptionContractIndex,
    cost_model: CostModel,
    lot_size_for: Callable[[dt.date], int],
    instrument: str = "NIFTY",
    exchange: str = "NFO",
    stop_pct: Decimal = Decimal(20),
    target_pct: Decimal = Decimal(20),
    max_hold: dt.timedelta = dt.timedelta(hours=3),
    strikes_out_of_the_money: int = 0,
    trial_ledger: TrialLedger | None = None,
    run_id: str = "manual",
) -> dict[str, BacktestResult]:
    """Backtests many strategies in ONE pass over history.

    The optimisation that makes the full library practical, and it is exact
    rather than approximate: the derived session frame depends only on the
    bars and the date, never on which strategy is asking, so all of them can
    share a single `StrategyContext` per day and therefore a single cached
    frame. Walking the 600 sessions once per strategy instead re-derives the
    identical frame 32 times — measured at ~8 minutes per strategy, so over
    four hours for the library, against roughly one pass here.

    Strategies never see each other: each keeps its own instance and its own
    `last_signal`, and none of them writes to the shared frame.
    """
    cached_store = _WholeSymbolCache(store.root, store)
    sessions = _sessions(store, instrument)
    # The REAL expiry calendar, from the contract archive itself — not a
    # weekday guess. See `_derive_session`'s docstring for why this matters:
    # NIFTY's weekly expiry moved Thursday -> Tuesday inside this data, and
    # `ExpiryDayOnly`'s weekday default mismatched 83 of 125 real expiries.
    expiry_dates = frozenset(contracts.expiries)

    trades: dict[str, list[StrategyTrade]] = {name: [] for name in strategy_names}
    unlabelled: dict[str, int] = dict.fromkeys(strategy_names, 0)

    previous_session: pd.DataFrame | None = None
    for day, frame in sessions:
        day_store = _DaySlice(store.root, frame)
        first = dt.datetime.combine(day, dt.time(9, 16), tzinfo=IST)
        last = dt.datetime.combine(day, LAST_ENTRY, tzinfo=IST)
        # ONE context, shared by every strategy for this day, carrying the
        # day's derived frame built ONCE. Profiling showed 88% of a run
        # inside `_session_frame` rebuilding identical columns 375 times a
        # day; this makes each evaluation a slice instead.
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
        while as_of <= last:
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
                trade = _label(
                    store=cached_store,
                    contracts=contracts,
                    cost_model=cost_model,
                    lot_size_for=lot_size_for,
                    index_level=Decimal(int(signal.entry_premium)) / 100,
                    entry_ts=evaluation.timestamp,
                    direction=signal.direction,
                    exchange=exchange,
                    stop_pct=stop_pct,
                    target_pct=target_pct,
                    max_hold=max_hold,
                    strikes_out_of_the_money=strikes_out_of_the_money,
                )
                if trade is None:
                    unlabelled[name] += 1
                else:
                    trades[name].append(trade)
            as_of += dt.timedelta(minutes=1)

    # Scored as ONE batch, so every strategy in this run is judged against
    # the same trial count rather than against however many happened to be
    # recorded before it in the loop.
    scored = score_many(
        {name: [t.r_multiple for t in trades[name]] for name in strategy_names},
        scope=TRIAL_SCOPE,
        config_hash_for={name: f"{name}:{instrument}" for name in strategy_names},
        trial_ledger=trial_ledger,
        run_id=run_id,
    )
    return {
        name: _assemble(
            strategy=name,
            instrument=instrument,
            scored=scored[name],
            trade_count=len(trades[name]),
            unlabelled=unlabelled[name],
            first_day=sessions[0][0] if sessions else None,
            last_day=sessions[-1][0] if sessions else None,
        )
        for name in strategy_names
    }


class _DaySlice(BarStore):
    """Serves `read()` from one preloaded day-frame.

    Swaps only STORAGE — every point-in-time rule still lives in
    `bars_asof`, which filters whatever this returns. Without it a
    600-session x 375-minute x 32-strategy run issues millions of Parquet
    reads.
    """

    def __init__(self, root: Path | str, frame: pd.DataFrame) -> None:
        super().__init__(root)
        self._frame = frame.sort_values("event_ts").reset_index(drop=True)

    def read(
        self,
        symbol: str,
        start: dt.datetime,
        end: dt.datetime,
        interval: str,
        ingested_before: dt.datetime | None = None,
    ) -> pd.DataFrame:
        del symbol
        df = self._frame
        if df.empty:
            return pd.DataFrame(columns=BAR_COLUMNS)
        mask = (df["event_ts"] >= pd.Timestamp(start)) & (df["event_ts"] <= pd.Timestamp(end))
        if ingested_before is not None:
            mask &= df["ingested_at"] <= pd.Timestamp(ingested_before)
        return df.loc[mask].reset_index(drop=True)


def _label(
    *,
    store: BarStore,
    contracts: OptionContractIndex,
    cost_model: CostModel,
    lot_size_for: Callable[[dt.date], int],
    index_level: Decimal,
    entry_ts: dt.datetime,
    direction: str,
    exchange: str,
    stop_pct: Decimal,
    target_pct: Decimal,
    max_hold: dt.timedelta,
    strikes_out_of_the_money: int,
) -> StrategyTrade | None:
    contract = contracts.nearest(
        on=entry_ts.astimezone(IST).date(),
        index_level=index_level,
        option_type="CE" if direction == "long_call" else "PE",
        strikes_out_of_the_money=strikes_out_of_the_money,
    )
    if contract is None:
        return None
    lot_size = lot_size_for(entry_ts.date())
    label = label_one_firing_on_premium(
        store=store,
        option_symbol=contract.symbol,
        entry_ts=entry_ts,
        stop_pct=stop_pct,
        target_pct=target_pct,
        max_hold=max_hold,
        cost_model=cost_model,
        exchange=exchange,
        lot_size=lot_size,
    )
    if label is None:
        return None

    # EVERY outcome is priced the same way: find the premium the position
    # actually exited at, then net the real round trip off it.
    #
    # The barrier branches used to short-circuit this with constants —
    # `+target_pct/stop_pct` for a target and exactly `-1.0` for a stop. The
    # target constant was right (the cost is already inside `target_level`,
    # so reaching it nets the full target distance), but the stop constant
    # was NOT: a stopped-out trade still pays the round trip, so it loses
    # `stop_distance + cost`, not `stop_distance`. Measured on the real cost
    # table, that is -1.03 to -1.06 R rather than -1.00 — so every losing
    # trade in every single-leg backtest was understated by 3-6% of an R,
    # always in the direction that flatters the strategy.
    stop_level, target_level = premium_barrier_levels(
        entry_premium=label.entry_premium,
        stop_pct=stop_pct,
        target_pct=target_pct,
        cost_model=cost_model,
        exchange=exchange,
        lot_size=lot_size,
        on=entry_ts.date(),
    )
    if label.barrier == "target":
        exit_premium = target_level
    elif label.barrier == "stop":
        exit_premium = stop_level
    else:
        # A time exit's P&L is not implied by the barrier — it has to be
        # read. Scoring time exits as flat is how a sweep misreports a rule
        # whose moves are real but slower than the horizon.
        exit_bars = bars_asof(store, contract.symbol, label.resolved_at, dt.timedelta(minutes=10), interval="1m")
        if exit_bars.empty:
            return None
        exit_premium = Paise(int(round(float(exit_bars.iloc[-1]["c"]) * 100)))

    round_trip = cost_model.round_trip(
        entry_premium=label.entry_premium,
        exit_premium=exit_premium,
        qty=lot_size,
        exchange=exchange,
        on=entry_ts.date(),
    ).total
    stop_distance = float(label.entry_premium) * float(stop_pct) / 100
    net = float(exit_premium) - float(label.entry_premium) - float(round_trip) / lot_size
    r_multiple = net / stop_distance if stop_distance else 0.0

    return StrategyTrade(
        entry_ts=entry_ts,
        direction=direction,
        option_symbol=contract.symbol,
        barrier=label.barrier,
        r_multiple=r_multiple,
    )


def _score(
    *,
    strategy: str,
    instrument: str,
    trades: list[StrategyTrade],
    unlabelled: int,
    first_day: dt.date | None,
    last_day: dt.date | None,
    trial_ledger: TrialLedger | None,
    run_id: str,
) -> BacktestResult:
    """Scores ONE strategy. `run_many` uses `score_many` instead, so that a
    whole library is judged against a single trial count; this remains for
    callers measuring a single strategy on its own."""
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
        trade_count=len(trades),
        unlabelled=unlabelled,
        first_day=first_day,
        last_day=last_day,
    )


def _assemble(
    *,
    strategy: str,
    instrument: str,
    scored: ScoredSample | None,
    trade_count: int,
    unlabelled: int,
    first_day: dt.date | None,
    last_day: dt.date | None,
) -> BacktestResult:
    """Wraps a scored sample (or the absence of one) in a `BacktestResult`.
    An unscored strategy reports zeros, never a placeholder that reads like
    a measurement."""
    if scored is None:
        return BacktestResult(
            strategy=strategy,
            instrument=instrument,
            trades=trade_count,
            unlabelled=unlabelled,
            win_rate=0.0,
            mean_r=0.0,
            sharpe=0.0,
            t_stat=0.0,
            deflated=0.0,
            n_trials_at_scoring=0,
            first_day=first_day,
            last_day=last_day,
        )
    return BacktestResult(
        strategy=strategy,
        instrument=instrument,
        trades=scored.n,
        unlabelled=unlabelled,
        win_rate=scored.win_rate,
        mean_r=scored.mean_r,
        sharpe=scored.sharpe,
        t_stat=scored.t_stat,
        deflated=scored.deflated,
        n_trials_at_scoring=scored.n_trials_at_scoring,
        first_day=first_day,
        last_day=last_day,
    )
