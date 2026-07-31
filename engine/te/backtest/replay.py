"""Replays the ORB rule over recorded history to produce labelled ML samples.

### Why

The meta-model needs on the order of 1,000 labelled firings. Collecting them
forward is hopeless arithmetic: the edge-triggered ORB fires a couple of
times a day across four underlyings, and `max_entries_per_underlying_per_day`
caps entries at 2, so forward collection reaches 1,000 samples somewhere
around 2028. Replaying the same rule over the ~225 trading days of
regime-consistent history already on disk produces them now.

### What makes this trustworthy rather than a second implementation

Nothing about the strategy is reimplemented here. This module walks a clock
and calls the **live** `OrbStrategy` through the **live** `StrategyContext`,
which reads through the **live** `bars_asof` point-in-time gate. A replay
firing is therefore the same object a live cycle would have produced at that
minute, and `te.ml.labeling.label_firings` — which reads
`cycle_evaluations` — consumes replay and live firings identically.

`_DayCacheStore` swaps only the *storage* behind that gate, never the gate
itself: `bars_asof` still applies `close_ts <= as_of AND ingested_at <=
as_of` to whatever `read()` returns. Without it, a 225-day x 4-symbol x
~350-minute replay would issue ~315,000 Parquet reads.

### Scope: only `verdict == "traded"` evaluations are persisted

`label_firings` selects on `verdict == "traded"`; nothing reads a historical
skip. Recording all ~315,000 skip rows (plus their conditions) would add
millions of rows to the transactional DB for no consumer. Skips are counted
and returned in `ReplayResult` so the funnel stays visible, but only firings
are written.

Cycles are recorded with `mode="replay"`, never `"paper"`, so a replayed
firing can always be told apart from one the live engine actually took.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from te.data.barstore import BAR_COLUMNS, BarStore
from te.domain.clock import DEFAULT_SESSION, IST, SessionWindow
from te.domain.evaluation import Evaluation
from te.persistence.db import session_scope
from te.persistence.models import CycleEvaluationRow
from te.persistence.repos.paper_trading import record_cycle, record_evaluation
from te.strategy.context import StrategyContext
from te.strategy.registry import get as get_strategy

logger = structlog.get_logger(__name__)

#: Replay stops entering at the same wall-clock time the live engine does
#: (`Settings.paper_cycle_hard_exit_by`), so the replayed firing
#: distribution matches the tradeable one. A firing at 15:25 would be
#: force-closed on the same cycle live and must not become a training row.
DEFAULT_LAST_ENTRY = dt.time(15, 20)


@dataclass
class ReplayResult:
    days: int = 0
    evaluations: int = 0
    firings: int = 0
    skips: int = 0
    #: Skip reason -> count. The funnel, kept so "why so few firings?" is
    #: answerable from the result object rather than by re-running.
    skip_reasons: dict[str, int] = field(default_factory=dict)
    firings_by_instrument: dict[str, int] = field(default_factory=dict)


class _DayCacheStore(BarStore):
    """A `BarStore` that serves `read()` from one preloaded day-frame.

    Read-only by construction: `append` is not overridden and must never be
    called on this class — it would write into the real root. Slicing here
    replaces only disk access; every point-in-time rule still lives in
    `bars_asof`, which filters whatever this returns.
    """

    def __init__(self, root: Path | str, frame: pd.DataFrame) -> None:
        super().__init__(root)
        # Sorted ONCE here, not per read. `read()` runs once per replayed
        # minute (~315,000 times for a full backfill), and the frame is a
        # single symbol's single day, so re-sorting it every call was pure
        # repeated work on already-ordered rows.
        self._frame = frame.sort_values("event_ts").reset_index(drop=True)

    def read(
        self,
        symbol: str,
        start: dt.datetime,
        end: dt.datetime,
        interval: str,
        ingested_before: dt.datetime | None = None,
    ) -> pd.DataFrame:
        del symbol  # single-symbol frame by construction — see `replay_orb`
        df = self._frame
        if df.empty:
            return pd.DataFrame(columns=BAR_COLUMNS)
        mask = (df["event_ts"] >= pd.Timestamp(start)) & (df["event_ts"] <= pd.Timestamp(end))
        if ingested_before is not None:
            mask &= df["ingested_at"] <= pd.Timestamp(ingested_before)
        return df.loc[mask].reset_index(drop=True)


def _days_with_bars(store: BarStore, symbol: str, start: dt.date, end: dt.date) -> list[tuple[dt.date, pd.DataFrame]]:
    """`(IST date, that day's bars)` for every day that actually HAS bars.

    Days come from the data rather than a weekday calculation, so exchange
    holidays need no separate calendar. The frame is read once for the whole
    range and grouped in memory: reading it only to extract the date list and
    then re-reading each day individually re-opened the same `month=`
    partitions ~225 more times per symbol, for identical rows.
    """
    frame = store.read(
        symbol=symbol,
        start=dt.datetime.combine(start, dt.time.min, tzinfo=dt.UTC),
        end=dt.datetime.combine(end, dt.time.max, tzinfo=dt.UTC),
        interval="1m",
    )
    if frame.empty:
        return []
    local_dates = frame["event_ts"].dt.tz_convert(IST).dt.date
    return list(frame.groupby(local_dates, sort=True))


def replay_orb(
    *,
    store: BarStore,
    session_factory: sessionmaker[Session],
    instruments: dict[str, str],
    start: dt.date,
    end: dt.date,
    strategy_name: str = "orb",
    session_window: SessionWindow = DEFAULT_SESSION,
    opening_range_minutes: int = 15,
    last_entry_by: dt.time = DEFAULT_LAST_ENTRY,
) -> ReplayResult:
    """Walks every recorded minute of `[start, end]` for each instrument and
    persists every ORB firing as a `cycle_evaluations` row.

    `instruments` maps symbol -> exchange (e.g. `{"NIFTY": "NSE_INDEX"}`).
    """
    result = ReplayResult()

    for symbol, exchange in instruments.items():
        for day, frame in _days_with_bars(store, symbol, start, end):
            day_store = _DayCacheStore(store.root, frame)

            # First evaluable minute is the close of the bar that ends the
            # opening range; before that `OrbStrategy` can only report
            # "opening range not yet formed".
            first = dt.datetime.combine(day, session_window.start, tzinfo=IST) + dt.timedelta(
                minutes=opening_range_minutes
            )
            last = dt.datetime.combine(day, last_entry_by, tzinfo=IST)

            firings_this_day = _replay_one_day(
                day_store=day_store,
                session_factory=session_factory,
                symbol=symbol,
                exchange=exchange,
                strategy_name=strategy_name,
                first=first,
                last=last,
                result=result,
            )
            result.days += 1
            if firings_this_day:
                result.firings_by_instrument[symbol] = result.firings_by_instrument.get(symbol, 0) + firings_this_day

        logger.info(
            "replay finished for instrument", symbol=symbol, firings=result.firings_by_instrument.get(symbol, 0)
        )

    return result


def _replay_one_day(
    *,
    day_store: _DayCacheStore,
    session_factory: sessionmaker[Session],
    symbol: str,
    exchange: str,
    strategy_name: str,
    first: dt.datetime,
    last: dt.datetime,
    result: ReplayResult,
) -> int:
    """One instrument, one day. Opens a single DB transaction for the day —
    a transaction per minute would dominate the runtime, and a day is a
    natural unit to lose and retry."""
    firings = 0
    as_of = first
    pending: list[Evaluation] = []

    # Resolved once: the registry hands back the same entry every time, and
    # this loop runs once per replayed minute. `OrbStrategy` resets
    # `last_signal` at the top of every `evaluate()`, so one instance across
    # the day carries no state forward.
    strategy = get_strategy(strategy_name)
    while as_of <= last:
        ctx = StrategyContext(store=day_store, instrument=symbol, exchange=exchange, as_of=as_of)
        evaluation = strategy.evaluate(ctx)
        result.evaluations += 1

        if evaluation.verdict == "traded":
            pending.append(evaluation)
            firings += 1
            result.firings += 1
        else:
            result.skips += 1
            result.skip_reasons[evaluation.reason] = result.skip_reasons.get(evaluation.reason, 0) + 1

        as_of += dt.timedelta(minutes=1)

    if not pending:
        return firings

    with session_scope(session_factory) as session:
        # Idempotent: `cycle_evaluations.evaluation_id` is UNIQUE, and
        # `OrbStrategy` derives it from (strategy, instrument, as_of), so
        # re-running a window that has already been replayed would abort the
        # whole run on a constraint violation. A long backfill has to be
        # resumable after a broker timeout, so already-persisted firings are
        # skipped rather than re-inserted.
        existing = {
            row[0]
            for row in session.execute(
                select(CycleEvaluationRow.evaluation_id).where(
                    CycleEvaluationRow.evaluation_id.in_([e.id for e in pending])
                )
            )
        }
        fresh = [e for e in pending if e.id not in existing]
        if not fresh:
            return firings
        cycle_id = record_cycle(session, ts=first, mode="replay")
        for evaluation in fresh:
            record_evaluation(session, cycle_id=cycle_id, evaluation=evaluation)

    return firings
