"""`SessionRule` — the base every plug-in strategy is written against.

### The problem it solves

`Strategy.evaluate(ctx)` is called once per instrument per minute and must
return a full `Evaluation` with honest `ConditionResult`s. Written directly,
that is ~80 lines of identical boilerplate per strategy: fetch bars, convert
to IST, slice to today's session, derive `minutes_from_open`, attach the
previous close, short-circuit the conditions, build the `Signal`, build the
`Evaluation`. `te.strategy.orb` does exactly that, and doing it thirty more
times would guarantee thirty subtly different versions.

Worse, several of those steps are where lookahead gets introduced. Centralising
them means the point-in-time discipline is written and tested ONCE:

* bars come only from `ctx.bars()`, which is `bars_asof` — the
  `close_ts <= as_of AND ingested_at <= as_of` gate;
* the frame handed to `decide()` is truncated at `ctx.as_of`, so a rule
  cannot see a later bar even by accident;
* `previous_close` is attached from the prior session, never from today.

A subclass therefore writes one method containing only its actual idea.

### What a subclass must provide

    class MyRule(SessionRule):
        spec = StrategySpec(name="my_rule", family="breakout", summary="...")

        def decide(self, session, params):
            ...
            return RuleDecision.enter("long_call", conditions=[...])

`session` is today's bars up to and including now, with `ist`,
`minutes_from_open` and `previous_close` already attached. `decide` returns
either an entry or a skip WITH A REASON — there is no third option, because
a skip with no stated reason is how a decision log becomes unauditable.

### Why the direction, and not the contract

`decide()` returns `long_call`/`long_put` and nothing else. Which strike and
expiry that becomes is resolved later by `te.engine.contract`, from live
broker data — a strategy that picked its own contract would be re-deriving
that logic thirty times and would drift from what the engine actually trades.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pandas as pd

from te.data.asof import interval_to_timedelta
from te.domain.clock import DEFAULT_SESSION, IST
from te.domain.evaluation import ConditionResult, Evaluation
from te.domain.money import Paise
from te.domain.signal import Direction, Signal
from te.strategy.context import StrategyContext
from te.strategy.indicators import SESSION_OPEN_HOUR, SESSION_OPEN_MINUTE
from te.strategy.spec import StrategySpec

#: How much history a rule may look back over within the session. Seven
#: hours covers a full NSE session (09:15-15:30) with margin, so a rule
#: asking for "the last 60 bars" always has them once the day is old enough.
_LOOKBACK = dt.timedelta(hours=7)

#: Separate, much wider window used ONCE per day to find the previous
#: session's close/high/low.
#:
#: `_LOOKBACK` cannot do this job, and assuming it could was a real bug. At
#: 09:16 a seven-hour window reaches back to 02:16 THE SAME MORNING — it
#: never touches yesterday's 15:30 close. Every rule needing the previous
#: session (both gap rules, both pivot rules, the previous-day breakout)
#: therefore saw `NaN` and skipped with "no previous session recorded", on
#: every bar of every day. Five of the thirty-two strategies would have been
#: permanently dead in live trading, and silently so: skipping with a
#: plausible reason looks identical to a rule that simply had no setup.
#:
#: Caught by `tests/strategy/test_prebuilt_matches_live_path.py`, which
#: compared the live path against the backtest path and found them
#: disagreeing.
#:
#: Five days covers a weekend plus a public holiday. It is read once per
#: session — `_SessionCache.previous` holds the result for the rest of the
#: day — so the wider scan costs one read per day, not one per minute.
_PREVIOUS_SESSION_LOOKBACK = dt.timedelta(days=5)

_NOT_REACHED = "not reached"

#: Key the incremental session frame is memoised under in `ctx.state`.
_CACHE_KEY = "_session_cache"

#: Key a BACKTEST may use to hand in a whole day's derived frame up front.
#:
#: Profiling a 20-session run showed 88% of the time inside `_session_frame`
#: — rebuilding the same columns 375 times a day — and only ~10% actually
#: running strategy logic. Precomputing the day once and slicing it per
#: minute removes that entirely.
#:
#: It is EXACTLY equivalent, not an approximation, and that rests on a
#: property worth stating: every derived column is either per-row (`ist`,
#: `minutes_from_open`) or constant for the session (`previous_close`,
#: `previous_high`, `previous_low`). None is a running function of earlier
#: rows, so a row's derived values are identical whether the frame was built
#: incrementally or all at once. The slice below then enforces the same
#: visibility rule `bars_asof` applies.
#:
#: The LIVE path never sets this and is unchanged — it cannot be, since
#: tomorrow's bars do not exist yet.
_PREBUILT_KEY = "_prebuilt_session"


@dataclass
class _SessionCache:
    """Per-session memo for `SessionRule._session_frame`.

    A typed object rather than a loose dict: it holds three values of three
    different types, and the mixed-type dict version defeated `mypy --strict`
    on every access.
    """

    date: dt.date
    frame: pd.DataFrame | None = None
    #: `(close, high, low)` of the PREVIOUS session; fixed for the whole day.
    previous: tuple[float, float, float] | None = None
    #: The `as_of` the cached frame was last built for. When a second
    #: strategy asks at the SAME instant the frame is already correct, so the
    #: bar read can be skipped entirely — see `_session_frame`.
    built_for: dt.datetime | None = None


@dataclass
class PrebuiltSession:
    """A whole session's derived frame, sliced per minute by a backtest.

    Built by `te.backtest.strategy_lab`, never by the live engine. Put it in
    `ctx.state[_PREBUILT_KEY]` and `SessionRule._session_frame` will serve
    every evaluation for that day from it.

    The visibility rule is the one thing this class exists to get exactly
    right, because it is where lookahead would enter. `bars_asof` shows a bar
    only once it has CLOSED (`close_ts = event_ts + interval <= as_of`), so a
    bar stamped 12:00 becomes visible at 12:01, not at 12:00. `visible_at`
    reproduces that rule with a binary search instead of a boolean mask.
    `tests/strategy/test_prebuilt_matches_live_path.py` asserts the two paths
    return identical frames minute by minute.
    """

    frame: pd.DataFrame
    #: `event_ts` as int64 nanoseconds, for `searchsorted`. Precomputed
    #: because converting it per minute would reintroduce the per-call cost
    #: this class removes.
    _stamps: Any = None

    def __post_init__(self) -> None:
        self._stamps = self.frame["event_ts"].to_numpy(dtype="datetime64[ns]")

    def visible_at(self, as_of: dt.datetime, *, interval: str = "1m") -> pd.DataFrame | None:
        cutoff = pd.Timestamp(as_of - interval_to_timedelta(interval)).to_datetime64()
        # `side="right"` so a bar whose close lands exactly on `as_of` IS
        # included — that is the boundary `bars_asof` uses (`<=`, not `<`).
        end = int(self._stamps.searchsorted(cutoff, side="right"))
        if end <= 0:
            return None
        return self.frame.iloc[:end]


def not_reached(label: str) -> ConditionResult:
    """A condition short-circuited by an earlier failure. Shared so every
    strategy reports the same honest shape."""
    return ConditionResult(
        label=label,
        required="n/a — short-circuited by an earlier failed/unreached condition",
        actual=_NOT_REACHED,
        passed=False,
        evaluated=False,
    )


@dataclass(frozen=True)
class RuleDecision:
    """What a rule concluded this minute.

    `direction is None` means skip, and `reason` is then REQUIRED — the
    dashboard's decision log shows it verbatim, and "skipped" with no
    explanation is exactly the unaccountable output this project exists to
    avoid.
    """

    direction: Direction | None
    conditions: list[ConditionResult] = field(default_factory=list)
    reason: str = ""

    def __post_init__(self) -> None:
        if self.direction is None and not self.reason:
            raise ValueError("a skip must state a reason — an unexplained skip is not auditable")

    @classmethod
    def enter(cls, direction: Direction, *, conditions: list[ConditionResult], reason: str = "") -> RuleDecision:
        return cls(direction=direction, conditions=conditions, reason=reason or "all conditions passed")

    @classmethod
    def skip(cls, reason: str, *, conditions: list[ConditionResult] | None = None) -> RuleDecision:
        return cls(direction=None, conditions=conditions or [], reason=reason)


class SessionRule(ABC):
    """Base for every plug-in strategy. Satisfies the `Strategy` Protocol."""

    spec: ClassVar[StrategySpec]

    def __init__(self, params: dict[str, Any] | None = None, *, name: str | None = None) -> None:
        self.params = self.spec.resolve(params)
        # `name` overrides the recorded strategy name for a PARAMETER
        # VARIANT. It matters because the name identifies a firing
        # downstream: `evaluation_id` is f"{name}-{instrument}-{as_of}" and
        # is UNIQUE, so two sweeps of the same minute under different
        # parameters collide unless the name differs. Found the hard way on
        # 2026-07-31, when a range-length sweep silently merged six
        # parameter settings into one dataset.
        self.name = name or self.spec.name
        self.last_signal: Signal | None = None

    @abstractmethod
    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        """The rule itself. `session` is today's visible bars, oldest first,
        ending at the current minute."""

    def evaluate(self, ctx: StrategyContext) -> Evaluation:
        self.last_signal = None
        session = self._session_frame(ctx)

        if session is None or session.empty:
            return self._skip(ctx, [], "no bars recorded for this session yet")

        decision = self.decide(session, self.params)
        if decision.direction is None:
            return self._skip(ctx, decision.conditions, decision.reason)

        close = float(session["c"].iloc[-1])
        self.last_signal = Signal(
            strategy=self.name,
            instrument=ctx.instrument,
            direction=decision.direction,
            # The INDEX level, not an option premium. `te.engine.contract`
            # replaces this with the real quoted premium once it has
            # resolved the contract; carrying the index level here is what
            # lets the strike be chosen from the level at decision time.
            entry_premium=Paise(int(round(close * 100))),
            lot_size=int(self.params.get("lot_size", 0)) or 65,
            ts=ctx.as_of,
        )
        return Evaluation(
            id=self._eval_id(ctx),
            timestamp=ctx.as_of,
            strategy=self.name,
            instrument=ctx.instrument,
            verdict="traded",
            reason=decision.reason,
            conditions=tuple(decision.conditions),
        )

    def _session_frame(self, ctx: StrategyContext) -> pd.DataFrame | None:
        """Today's visible bars, with the derived columns rules rely on.

        **Built incrementally, and that is a correctness-preserving
        optimisation rather than a shortcut.** A backtest evaluates once per
        minute — ~375 times a day, ~600 days, across 32 strategies, which is
        roughly 7 million calls. Re-deriving the whole session each time
        (timezone conversion, dedup, sort, date filter, previous-session
        lookup over a 7-hour window) made a single strategy take over ten
        minutes, so the full library was hours.

        The cache lives in `ctx.state`, which exists for exactly this, and is
        keyed by session date so it resets at every day boundary. Only bars
        NEWER than the last call are converted and appended; nothing already
        derived is recomputed, and nothing beyond `ctx.as_of` is ever read
        because `ctx.bars()` is still the only source. A caller that creates
        a fresh context per minute simply gets the old behaviour — correct,
        just slower — so this can never change a decision.
        """
        prebuilt = ctx.state.get(_PREBUILT_KEY)
        if isinstance(prebuilt, PrebuiltSession):
            return prebuilt.visible_at(ctx.as_of, interval=ctx.interval)

        as_of_local = ctx.as_of.astimezone(IST)
        today_date = as_of_local.date()
        cache = ctx.state.get(_CACHE_KEY)
        if not isinstance(cache, _SessionCache) or cache.date != today_date:
            cache = _SessionCache(date=today_date)
            ctx.state[_CACHE_KEY] = cache

        # SAME INSTANT, ALREADY BUILT -> return it without reading anything.
        #
        # This is what makes a whole-library backtest practical. The frame
        # depends only on the bars and the timestamp, never on which strategy
        # is asking, so when 32 strategies are evaluated at the same minute
        # only the first does any work. Without it each of them called
        # `bars_asof` separately — 32 reads per minute, ~6.5 million over a
        # full run, which left the library backtest still going after 100
        # minutes. Sharing the derived frame alone was not enough; the READ
        # had to be shared too.
        if cache.built_for == ctx.as_of and cache.frame is not None and not cache.frame.empty:
            return cache.frame

        bars = ctx.bars(lookback=_LOOKBACK)
        if bars.empty:
            return None

        last_seen = None if cache.frame is None or cache.frame.empty else cache.frame["event_ts"].iloc[-1]
        fresh = bars if last_seen is None else bars[bars["event_ts"] > last_seen]
        if fresh.empty:
            # Stamped even on the no-new-bars path, so the next strategy
            # asking at this same instant also short-circuits.
            cache.built_for = ctx.as_of
            return cache.frame if cache.frame is not None and not cache.frame.empty else None

        fresh = fresh.drop_duplicates(subset="event_ts", keep="last").sort_values("event_ts").reset_index(drop=True)
        fresh["ist"] = fresh["event_ts"].dt.tz_convert(IST)

        # The previous session's close/high/low — the only cross-day values
        # any rule uses, and what the gap and pivot rules need. Resolved ONCE
        # per day: it cannot change as the session progresses, so recomputing
        # it every minute was pure waste.
        if cache.previous is None:
            # Deliberately a SEPARATE, wider read — see
            # `_PREVIOUS_SESSION_LOOKBACK`. Done once per session, not per
            # minute, so the cost is one scan a day.
            history = ctx.bars(lookback=_PREVIOUS_SESSION_LOOKBACK)
            earlier = (
                history[history["event_ts"].dt.tz_convert(IST).dt.date < today_date]
                if not history.empty
                else history
            )
            if earlier.empty:
                cache.previous = (float("nan"), float("nan"), float("nan"))
            else:
                earlier_dates = earlier["event_ts"].dt.tz_convert(IST).dt.date
                previous_day = earlier[earlier_dates == earlier_dates.max()]
                cache.previous = (
                    float(previous_day["c"].iloc[-1]),
                    float(previous_day["h"].max()),
                    float(previous_day["l"].min()),
                )

        fresh_today = fresh[fresh["ist"].dt.date == today_date].reset_index(drop=True)
        if fresh_today.empty:
            # Stamped even on the no-new-bars path, so the next strategy
            # asking at this same instant also short-circuits.
            cache.built_for = ctx.as_of
            return cache.frame if cache.frame is not None and not cache.frame.empty else None

        # Derived columns are computed on the NEW rows only, then appended.
        # Assigning them to the whole frame each minute made the work O(n)
        # per call and O(n^2) over a session — the single largest remaining
        # cost once the frame itself was cached.
        previous_close, previous_high, previous_low = cache.previous
        fresh_today["previous_close"] = previous_close
        fresh_today["previous_high"] = previous_high
        fresh_today["previous_low"] = previous_low
        # `minutes_from_open` measures from the SESSION's open, so it needs
        # the first bar of the day — which lives in the cached frame once
        # there is one, not in this slice.
        cached = cache.frame
        first_bar = (
            cached["ist"].iloc[0] if cached is not None and not cached.empty else fresh_today["ist"].iloc[0]
        )
        session_open = first_bar.replace(
            hour=SESSION_OPEN_HOUR, minute=SESSION_OPEN_MINUTE, second=0, microsecond=0
        )
        fresh_today["minutes_from_open"] = (fresh_today["ist"] - session_open).dt.total_seconds() / 60
        # The REAL expiry calendar, when the caller supplied one. Live it
        # comes from the broker's listed chain; in a backtest from the
        # contract archive. Same column either way, so a calendar-gated rule
        # cannot behave differently depending on who built its frame.
        if ctx.expiry_dates is not None:
            fresh_today["is_expiry_day"] = today_date in ctx.expiry_dates

        frame = (
            fresh_today
            if cache.frame is None or cache.frame.empty
            else pd.concat([cache.frame, fresh_today], ignore_index=True)
        )
        cache.frame = frame
        cache.built_for = ctx.as_of
        return frame

    def _skip(self, ctx: StrategyContext, conditions: list[ConditionResult], reason: str) -> Evaluation:
        return Evaluation(
            id=self._eval_id(ctx),
            timestamp=ctx.as_of,
            strategy=self.name,
            instrument=ctx.instrument,
            verdict="skipped",
            reason=reason,
            conditions=tuple(conditions),
        )

    def _eval_id(self, ctx: StrategyContext) -> str:
        return f"{self.name}-{ctx.instrument}-{ctx.as_of.isoformat()}"


def session_end(day: dt.date) -> dt.datetime:
    """Close of the regular session on `day`, in IST. Shared by the timing
    rules so none of them hardcodes 15:30."""
    return dt.datetime.combine(day, DEFAULT_SESSION.end, tzinfo=IST)
