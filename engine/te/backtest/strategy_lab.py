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
from te.risk.limits import consecutive_losses_breached, daily_loss_breached, drawdown_breached
from te.risk.sizing import size_position
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

#: Sizing defaults for `run_many`/`_label`. Deliberately NOT the real
#: `Settings.paper_cycle_*` risk config: these exist only so that
#: `size_position` structurally cannot reject a trade for an existing
#: caller that never asked for sizing in the first place
#: (`scripts/backtest_all_strategies.py` and the r-multiple regression
#: tests) — capital effectively unlimited, no minimum edge required, no
#: position-size cap. A caller that wants REAL affordability (e.g. the
#: daily rupee report) must pass its own `capital`/`risk_budget_pct`/
#: `max_position_size_pct`/`min_edge_multiple`.
_UNLIMITED_SIZING_CAPITAL = Paise(10**15)
_UNLIMITED_SIZING_RISK_BUDGET_PCT = Decimal(100)
_UNLIMITED_SIZING_MAX_POSITION_PCT = Decimal(100)
_UNLIMITED_SIZING_MIN_EDGE_MULTIPLE = Decimal(0)


@dataclass(frozen=True)
class StrategyTrade:
    entry_ts: dt.datetime
    direction: str
    option_symbol: str
    barrier: str
    #: Outcome in units of the stop distance: -1.0 is a full stop, +1.0 is
    #: one stop distance earned, both NET of real brokerage/STT/GST.
    r_multiple: float
    #: The real per-UNIT net (paise), NET of costs — what `r_multiple` is
    #: computed from, kept alongside it rather than only as a ratio. This is
    #: what makes real rupee reporting (`te.backtest.daily`) possible instead
    #: of only R-multiple/Sharpe reporting.
    net_paise_per_unit: int = 0
    #: How many lots `te.risk.sizing.size_position` fit inside the caller's
    #: capital/risk budget. Defaults to `1` for callers (and the direct
    #: `StrategyTrade(...)` fixtures in `tests/backtest/
    #: test_strategy_scoring.py`) that never asked for real sizing.
    lots: int = 1
    lot_size: int = 1
    #: The day the trade actually CLOSED — `PremiumLabel.resolved_at`. A
    #: rupee-per-day report keys on this, not `entry_ts`, per
    #: `te.backtest.daily`'s own convention. Defaults to `entry_ts` when not
    #: supplied so old fixtures that only cared about `r_multiple` remain
    #: valid without inventing a fake exit time.
    exit_ts: dt.datetime | None = None
    #: The three premiums `te.risk.sizing.size_position` needs, carried on
    #: the labelled trade so a caller can RE-SIZE it later without walking
    #: the option's premium bars again.
    #:
    #: That is what makes the parameter sweep (`te.backtest.sweep`) tractable:
    #: risk-per-trade changes only affordability and lot count, never the
    #: barrier outcome, so one labelling pass can be re-sized at four risk
    #: levels instead of being walked four times. Default `0` for the
    #: fixtures that construct `StrategyTrade` directly and never re-size.
    entry_premium_paise: int = 0
    stop_premium_paise: int = 0
    target_premium_paise: int = 0

    def __post_init__(self) -> None:
        if self.exit_ts is None:
            object.__setattr__(self, "exit_ts", self.entry_ts)


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
    #: Signals the strategy actually fired that a real premium/stop/target
    #: walked to a labelled outcome, but that `size_position` rejected as
    #: unaffordable at the caller's capital/risk budget — kept SEPARATE from
    #: `unlabelled` (which is data unavailability, not affordability) and
    #: from `trades` (which must only ever count trades that could actually
    #: have been placed). Per `honest-metrics`: a rejection you cannot see is
    #: a lie, and on small capital this can be most of the signals. Defaults
    #: to `0` for callers that never asked for real sizing (see
    #: `_UNLIMITED_SIZING_*` above), so those never see a false rejection.
    unaffordable: int = 0
    #: Days cut short by `max_daily_loss_paise` — the day's cumulative
    #: realized net breached it, so no further entries were taken THAT DAY.
    #: Per `honest-metrics`, this is reported alongside `trades`/
    #: `unaffordable`, never hidden: a run that silently skipped entries
    #: while reporting only the ones it took would overstate how much the
    #: strategy actually traded. Defaults to `0` for every caller that never
    #: asked for daily-loss enforcement (`max_daily_loss_paise=None`).
    halted_days: int = 0
    #: Days cut short by `max_consecutive_losses` — same day-scoped
    #: stand-down semantics as `te.risk.limits.check_consecutive_losses`.
    #: Defaults to `0` when `max_consecutive_losses=None`.
    standdown_days: int = 0
    #: Whether `max_drawdown_pct` (a persistent halt, unlike the two daily
    #: counters above) or an account wipe-out (equity <= 0 under
    #: `compound_equity=True`) stopped this strategy's run early. Defaults to
    #: `False` for every caller that asked for neither.
    drawdown_halted: bool = False
    #: `capital + cumulative realized net` at the end of the run (or at the
    #: point the run stopped, if `drawdown_halted`). Always computed, even
    #: when `compound_equity=False` (sizing then still uses the fixed
    #: `capital`, but the equity figure itself is real).
    final_equity_paise: int = 0
    #: Total realized net P&L in paise over the whole run — the money
    #: question, as opposed to `mean_r`'s unit-free one. Every strategy in
    #: this library had only ever been scored in R-multiples, which cannot
    #: answer "how much would this have made".
    net_pnl_paise: int = 0
    #: The capital the run was sized against. Stored WITH `net_pnl_paise`
    #: because a rupee P&L is meaningless without it: -Rs 6,000 is a fifth of
    #: a Rs 30,000 account and a rounding error on a Rs 30,00,000 one, and a
    #: reader cannot tell which from the P&L alone.
    capital_paise: int = 0

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
    #: Days-to-expiry band, INCLUSIVE. The defaults reproduce the previous
    #: behaviour exactly (`nearest`'s own 0..7), so every existing caller
    #: and every recorded regression result is unchanged — but the band is
    #: now something a caller can CHOOSE rather than inherit. See `_label`
    #: for why pooling expiries was a confound rather than a detail.
    min_days_to_expiry: int = 0,
    max_days_to_expiry: int = 7,
    capital: Paise = _UNLIMITED_SIZING_CAPITAL,
    risk_budget_pct: Decimal = _UNLIMITED_SIZING_RISK_BUDGET_PCT,
    max_position_size_pct: Decimal = _UNLIMITED_SIZING_MAX_POSITION_PCT,
    min_edge_multiple: Decimal = _UNLIMITED_SIZING_MIN_EDGE_MULTIPLE,
    trial_ledger: TrialLedger | None = None,
    run_id: str = "manual",
    collect_trades: bool = False,
    max_daily_loss_paise: int | None = None,
    max_consecutive_losses: int | None = None,
    max_drawdown_pct: Decimal | None = None,
    compound_equity: bool = False,
) -> dict[str, BacktestResult] | tuple[dict[str, BacktestResult], dict[str, list[StrategyTrade]]]:
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

    `collect_trades=False` (the default, and every pre-existing caller's
    behaviour) returns just the per-strategy summaries, matching
    `results_store.py`'s stated design that individual trades are not part
    of a `BacktestResult` — a 32-strategy x 600-session run keeping every
    trade in memory has no reason to when nothing downstream reads it.
    `collect_trades=True` additionally returns the real per-strategy trade
    lists (`StrategyTrade`, carrying real rupee P&L), for a caller like the
    daily rupee report that genuinely needs day-by-day money, not just the
    scored summary.

    ### Risk enforcement (opt-in, defaulted off)

    `max_daily_loss_paise`/`max_consecutive_losses`/`max_drawdown_pct`
    enforce the SAME rules the live engine does — `te.risk.limits.
    daily_loss_breached`/`consecutive_losses_breached`/`drawdown_breached`,
    the pure predicates `check_daily_loss_limit`/`check_consecutive_losses`/
    `check_max_drawdown` themselves call — never a re-implementation, so the
    two paths cannot silently drift apart. All three default to `None`
    (disabled), so every pre-existing caller (`scripts/
    backtest_all_strategies.py`, the r-multiple regression tests) is
    byte-for-byte unchanged.

    `max_daily_loss_paise`/`max_consecutive_losses` are day-scoped stand-
    downs (mirroring live): once breached, no further entries THAT DAY, but
    the flag clears the next day. `max_drawdown_pct` is a PERSISTENT halt
    (also mirroring live): once breached, that strategy takes no further
    entries for the REST OF THE RUN. Every day/strategy cut short this way is
    counted on the returned `BacktestResult` (`halted_days`/
    `standdown_days`/`drawdown_halted`), never silently absorbed — a run
    that skipped a third of its days while reporting only the days it kept
    would be exactly the kind of number `honest-metrics` forbids.

    `compound_equity=True` sizes every trade off CURRENT equity (`capital +
    cumulative realized net so far`) instead of the fixed starting `capital`
    — real money compounds; after losing money you are betting a smaller
    stake, not the original one. If equity drops to or below zero the
    account is wiped: the run stops for that strategy immediately
    (`drawdown_halted=True`) rather than continuing to size trades off
    negative capital. `final_equity_paise` on the result is always the
    ending `capital + cumulative realized net`, whether or not
    `compound_equity` was used for sizing.
    """
    # Did the caller ask for REAL sizing, or accept the unlimited defaults?
    # Only a real answer may be reported as money — see `_assemble` below.
    _sized_for_real = capital != _UNLIMITED_SIZING_CAPITAL

    cached_store = _WholeSymbolCache(store.root, store)
    sessions = _sessions(store, instrument)
    # The REAL expiry calendar, from the contract archive itself — not a
    # weekday guess. See `_derive_session`'s docstring for why this matters:
    # NIFTY's weekly expiry moved Thursday -> Tuesday inside this data, and
    # `ExpiryDayOnly`'s weekday default mismatched 83 of 125 real expiries.
    expiry_dates = frozenset(contracts.expiries)

    trades: dict[str, list[StrategyTrade]] = {name: [] for name in strategy_names}
    unlabelled: dict[str, int] = dict.fromkeys(strategy_names, 0)
    unaffordable: dict[str, int] = dict.fromkeys(strategy_names, 0)

    # Risk-enforcement state — tracked per strategy, persisting across the
    # whole run (unlike the day-scoped dicts reset inside the loop below).
    # `equity_paise` is always tracked (it is what `final_equity_paise`
    # reports), even for callers who never asked for `compound_equity` or any
    # of the three limits.
    equity_paise: dict[str, int] = dict.fromkeys(strategy_names, int(capital))
    peak_equity_paise: dict[str, int] = dict.fromkeys(strategy_names, int(capital))
    drawdown_halted: dict[str, bool] = dict.fromkeys(strategy_names, False)
    halted_days: dict[str, int] = dict.fromkeys(strategy_names, 0)
    standdown_days: dict[str, int] = dict.fromkeys(strategy_names, 0)

    # Trades ENTERED but not yet resolved. Their P&L does not exist until
    # `exit_ts`, so it must not reach equity, the daily net, or the
    # consecutive-loss streak before then.
    #
    # This used to credit the outcome at ENTRY. With up to three entries a
    # day that let the second and third be sized off money the first had not
    # yet made, and had the daily-loss limit checked against a result that
    # had not happened � an edge the live engine cannot possibly have.
    # Caught in review 2026-08-05.
    pending: dict[str, list[StrategyTrade]] = {name: [] for name in strategy_names}

    def _settle(name: str, upto: dt.datetime) -> None:
        """Credits every held trade that has resolved by `upto`, in
        resolution order, applying the risk limits as each lands."""
        due = [t for t in pending[name] if (t.exit_ts or t.entry_ts) <= upto]
        if not due:
            return
        pending[name] = [t for t in pending[name] if (t.exit_ts or t.entry_ts) > upto]
        for trade in sorted(due, key=lambda t: t.exit_ts or t.entry_ts):
            net_total = trade.net_paise_per_unit * trade.lot_size * trade.lots
            equity_paise[name] += net_total
            daily_net_paise[name] += net_total
            recent_trade_nets[name].insert(0, net_total)

            if max_drawdown_pct is not None:
                peak_equity_paise[name] = max(peak_equity_paise[name], equity_paise[name])
                if drawdown_breached(
                    current_equity_paise=equity_paise[name],
                    peak_equity_paise=peak_equity_paise[name],
                    max_drawdown_pct=max_drawdown_pct,
                ):
                    drawdown_halted[name] = True
            if compound_equity and equity_paise[name] <= 0:
                # The account is wiped. Stop the run for this strategy rather
                # than sizing further trades off negative capital. Gated on
                # `compound_equity`: a caller who never asked sizing to follow
                # equity must not have its run cut short by a tracked-but-
                # unused equity figure going negative.
                drawdown_halted[name] = True

            if (
                max_daily_loss_paise is not None
                and not day_loss_halted[name]
                and daily_loss_breached(
                    net_paise=daily_net_paise[name], max_daily_loss_paise=int(max_daily_loss_paise)
                )
            ):
                day_loss_halted[name] = True
                halted_days[name] += 1
            if (
                max_consecutive_losses is not None
                and not day_standdown[name]
                and consecutive_losses_breached(
                    recent_trade_nets=recent_trade_nets[name], limit=max_consecutive_losses
                )
            ):
                day_standdown[name] = True
                standdown_days[name] += 1

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
        # Day-scoped risk state — reset every day, unlike `drawdown_halted`/
        # `equity_paise` above, which persist across the whole run. Mirrors
        # live: a daily-loss halt or a consecutive-losses stand-down expires
        # with the trading day.
        daily_net_paise = dict.fromkeys(strategy_names, 0)
        recent_trade_nets: dict[str, list[int]] = {name: [] for name in strategy_names}
        day_loss_halted = dict.fromkeys(strategy_names, False)
        day_standdown = dict.fromkeys(strategy_names, False)

        as_of = first
        while as_of <= last:
            ctx.as_of = as_of
            for name in strategy_names:
                _settle(name, as_of)
            for name, strategy in strategies.items():
                if drawdown_halted[name]:
                    continue  # persistent halt — this strategy trades no more for the rest of the run
                if entries_today[name] >= MAX_ENTRIES_PER_DAY:
                    continue
                if max_daily_loss_paise is not None and day_loss_halted[name]:
                    continue
                if max_consecutive_losses is not None and day_standdown[name]:
                    continue
                evaluation = strategy.evaluate(ctx)
                if evaluation.verdict != "traded":
                    continue
                signal = getattr(strategy, "last_signal", None)
                if signal is None:
                    continue
                entries_today[name] += 1
                trade_capital = Paise(equity_paise[name]) if compound_equity else capital
                trade, was_unaffordable = _label(
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
                    min_days_to_expiry=min_days_to_expiry,
                    max_days_to_expiry=max_days_to_expiry,
                    capital=trade_capital,
                    risk_budget_pct=risk_budget_pct,
                    max_position_size_pct=max_position_size_pct,
                    min_edge_multiple=min_edge_multiple,
                )
                if trade is not None:
                    trades[name].append(trade)
                    # HELD until it actually resolves — see `_settle` below.
                    # Crediting here would size the day's later entries off
                    # money this trade has not made yet.
                    pending[name].append(trade)
                elif was_unaffordable:
                    unaffordable[name] += 1
                else:
                    unlabelled[name] += 1
            as_of += dt.timedelta(minutes=1)
        # Anything still open at the end of the day settles now: every exit
        # path is bounded by `max_hold` and the hard exit, so nothing can
        # legitimately survive the session.
        for name in strategy_names:
            _settle(name, as_of + dt.timedelta(days=1))

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
    results = {
        name: _assemble(
            strategy=name,
            instrument=instrument,
            scored=scored[name],
            trade_count=len(trades[name]),
            unlabelled=unlabelled[name],
            unaffordable=unaffordable[name],
            first_day=sessions[0][0] if sessions else None,
            last_day=sessions[-1][0] if sessions else None,
            halted_days=halted_days[name],
            standdown_days=standdown_days[name],
            drawdown_halted=drawdown_halted[name],
            # Money is reported ONLY when the caller asked for real sizing.
            #
            # With the `_UNLIMITED_SIZING_*` defaults, capital is Rs 10
            # trillion and `size_position` returns on the order of a billion
            # lots, so these figures come out in the 10^13 paise range.
            # `scripts/backtest_all_strategies.py` is exactly such a caller
            # and persists them through `save_results` — which would put
            # fabricated money on the Strategies page, the precise
            # `honest-metrics` failure this file's own docstring warns
            # about. An honest zero says "not measured"; a plausible-looking
            # number does not. Caught in review 2026-08-05.
            final_equity_paise=equity_paise[name] if _sized_for_real else 0,
            # Equity is seeded at `capital` and moved only by realized net,
            # so the difference IS the run's P&L — derived here rather than
            # accumulated separately so the two can never disagree.
            net_pnl_paise=(equity_paise[name] - int(capital)) if _sized_for_real else 0,
            capital_paise=int(capital) if _sized_for_real else 0,
        )
        for name in strategy_names
    }
    if collect_trades:
        return results, trades
    return results


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
    min_days_to_expiry: int = 0,
    max_days_to_expiry: int = 7,
    capital: Paise = _UNLIMITED_SIZING_CAPITAL,
    risk_budget_pct: Decimal = _UNLIMITED_SIZING_RISK_BUDGET_PCT,
    max_position_size_pct: Decimal = _UNLIMITED_SIZING_MAX_POSITION_PCT,
    min_edge_multiple: Decimal = _UNLIMITED_SIZING_MIN_EDGE_MULTIPLE,
) -> tuple[StrategyTrade | None, bool]:
    """Labels one firing on its real option premium path.

    Returns `(trade, unaffordable)`. `unaffordable` is `True` only when a
    firing walked all the way to a real labelled outcome and
    `te.risk.sizing.size_position` then rejected it as unaffordable at the
    caller's capital/risk budget — distinct from every other `None` case
    below (no contract, no label, no exit bars), which is missing DATA, not
    a rejected trade, and must not be counted as either a trade or an
    unaffordable signal.
    """
    # DAYS TO EXPIRY, controlled rather than inherited.
    #
    # Until 2026-08-05 this called `nearest` with its defaults, so every
    # buying result ever produced by this lab pooled whatever expiry
    # happened to be nearest — 0 days on expiry day, 6 the morning after
    # one. Those are not the same instrument: an option a day from expiry
    # has almost no time value left to lose and costs a third as much per
    # lot, and one six days out has plenty of both. Measured on the real
    # trade history, the same NIFTY lot cost Rs 2,746 on its expiry day and
    # Rs 8,976 six days out, and the two winning trades in the account were
    # both expiry-day.
    #
    # Pooling them produced an average that describes no tradeable
    # instrument — the same confound `SpreadGeometry.min_days_to_expiry`
    # documents for credit spreads, which the buying side never got.
    on = entry_ts.astimezone(IST).date()
    contract = contracts.nearest(
        on=on,
        index_level=index_level,
        option_type="CE" if direction == "long_call" else "PE",
        strikes_out_of_the_money=strikes_out_of_the_money,
        max_days_to_expiry=max_days_to_expiry,
        min_days_to_expiry=min_days_to_expiry,
    )
    if contract is None:
        return None, False
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
        return None, False

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
            return None, False
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

    # A real per-unit net was just computed above and used to be thrown
    # away here — everything from this point on is what makes a rupee
    # figure (not just an R-multiple) possible for this firing.
    sizing = size_position(
        capital=capital,
        risk_budget_pct=risk_budget_pct,
        premium=label.entry_premium,
        stop_premium=stop_level,
        target_premium=target_level,
        lot_size=lot_size,
        costs=cost_model,
        exchange=exchange,
        on=entry_ts.date(),
        min_edge_multiple=min_edge_multiple,
        max_position_size_pct=max_position_size_pct,
    )
    if sizing.lots == 0:
        # A real, labelled outcome that the capital/risk budget could not
        # have afforded. It must not silently vanish (that would make
        # small-capital rejection invisible) and must not be counted as a
        # trade (that would misrepresent what actually would have been
        # placed) — see `BacktestResult.unaffordable`.
        return None, True

    return StrategyTrade(
        entry_ts=entry_ts,
        direction=direction,
        option_symbol=contract.symbol,
        barrier=label.barrier,
        r_multiple=r_multiple,
        net_paise_per_unit=int(round(net)),
        lots=sizing.lots,
        lot_size=lot_size,
        exit_ts=label.resolved_at,
        # Carried so `te.backtest.sweep` can re-run `size_position` at a
        # different risk budget without re-walking the premium bars.
        entry_premium_paise=int(label.entry_premium),
        stop_premium_paise=int(stop_level),
        target_premium_paise=int(target_level),
    ), False


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
    unaffordable: int = 0,
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
        unaffordable=unaffordable,
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
    unaffordable: int = 0,
    halted_days: int = 0,
    standdown_days: int = 0,
    drawdown_halted: bool = False,
    final_equity_paise: int = 0,
    net_pnl_paise: int = 0,
    capital_paise: int = 0,
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
            unaffordable=unaffordable,
            halted_days=halted_days,
            standdown_days=standdown_days,
            drawdown_halted=drawdown_halted,
            final_equity_paise=final_equity_paise,
            net_pnl_paise=net_pnl_paise,
            capital_paise=capital_paise,
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
        unaffordable=unaffordable,
        halted_days=halted_days,
        standdown_days=standdown_days,
        drawdown_halted=drawdown_halted,
        final_equity_paise=final_equity_paise,
        net_pnl_paise=net_pnl_paise,
        capital_paise=capital_paise,
    )
