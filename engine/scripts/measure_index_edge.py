#!/usr/bin/env python
"""Does an ORB firing predict the INDEX at all, with no option in the way?

Usage:
    python -m scripts.measure_index_edge --strategy orb60 --instruments NIFTY

### Why this exists

Six stop/target geometries were swept over 2,605 firings on REAL option
premiums (2024-01-04..2026-05-05). Every one lost, and the loss grew
monotonically with target width — the signature of theta, not of noise.

That result condemns option BUYING. It does not, on its own, condemn the ORB
signal, because a long option carries three costs the index does not: time
decay, the bid-ask spread crossed twice, and statutory charges. A signal
with a small real directional edge can be comfortably profitable on the
underlying and still lose money wrapped in a decaying instrument.

The two cases lead to opposite decisions, so guessing between them is
expensive:

- **Index moves the right way** -> the entry rule is fine and the WRAPPER is
  wrong. That points at selling premium (credit spreads), where decay pays
  instead of charges, and at debit spreads as the affordable middle step.
- **Index does not move either** -> ORB has no edge on this instrument and
  no wrapper saves it. The next move is a different entry rule entirely.

### What is measured

For each firing: the index's own forward return over several horizons,
SIGNED BY DIRECTION (a `long_put` firing profits from a fall, so its return
is negated). A positive mean means the breakout predicted direction.

No costs are applied and none should be. The question here is not "is this
tradeable" — the barrier sweep already answered that for buying. It is "is
there any directional information", and loading it with option costs would
answer the first question again.

### Reading it honestly

`mean` alone proves nothing at n=2,605; a t-statistic near zero is the
expected outcome for a rule with no edge, and |t| > 2 is the conventional
bar for "probably not chance". The hit rate is reported beside it because a
mean can be dragged by a handful of large moves — a rule that is right 50%
of the time with one enormous winner is a different animal from one that is
right 55% of the time, and only the second is something to build on.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import statistics
import sys
from collections import defaultdict

from te.data.asof import bars_asof
from te.data.barstore import BarStore
from te.ml.labeling import direction_from_breakout
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import CycleEvaluationRow
from te.settings import Settings

#: Minutes after entry to measure the index at. Spans the range a firing can
#: realistically be held: the measured median winner took 40 minutes, and the
#: engine's max hold is 180.
HORIZONS = (15, 30, 60, 120, 180)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strategy", default="orb60")
    parser.add_argument("--instruments", default="NIFTY")
    parser.add_argument("--exchange", default="NSE_INDEX")
    args = parser.parse_args()

    settings = Settings()
    store = BarStore(settings.bar_store_path)
    session_factory = make_session_factory(make_engine(settings.database_url))

    chosen = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]

    print(f"INDEX-ONLY forward returns after an ORB firing ({args.strategy}).")
    print("Signed by direction: a long_put firing's return is negated, so positive = the")
    print("breakout predicted correctly. NO costs applied — this measures information,")
    print("not tradeability (the barrier sweep already measured tradeability).\n")

    for symbol in chosen:
        with session_factory() as session:
            rows = (
                session.query(CycleEvaluationRow)
                .filter(
                    CycleEvaluationRow.strategy == args.strategy,
                    CycleEvaluationRow.instrument == symbol,
                    CycleEvaluationRow.verdict == "traded",
                )
                .order_by(CycleEvaluationRow.ts)
                .all()
            )
            firings = [(row.ts, row.evaluation_id) for row in rows]

        if not firings:
            print(f"{symbol}: no firings recorded under strategy {args.strategy!r}", file=sys.stderr)
            continue

        returns: dict[int, list[float]] = defaultdict(list)
        # Same firings, but only those that have EVERY horizon available.
        # Without this the horizons are not comparable: a firing at 14:00
        # has no 180-minute forward return, so the long-horizon rows are
        # computed on a subset made almost entirely of EARLY firings. If
        # early firings behave differently — and a breakout at 10:15 plainly
        # might — then a rising mean across horizons could be measuring the
        # time of day rather than the holding period, and nothing in the
        # first table would show it.
        balanced: dict[int, list[float]] = defaultdict(list)
        unresolved = 0
        for entry_ts, evaluation_id in firings:
            direction = _direction_for(session_factory, evaluation_id)
            if direction is None:
                unresolved += 1
                continue
            entry_ts = entry_ts if entry_ts.tzinfo else entry_ts.replace(tzinfo=dt.UTC)
            entry_px = _close_at(store, symbol, entry_ts)
            if entry_px is None:
                unresolved += 1
                continue
            sign = 1.0 if direction == "long_call" else -1.0
            per_horizon: dict[int, float] = {}
            for horizon in HORIZONS:
                later = _close_at(store, symbol, entry_ts + dt.timedelta(minutes=horizon))
                if later is None:
                    continue
                bps = sign * (later - entry_px) / entry_px * 10_000
                returns[horizon].append(bps)
                per_horizon[horizon] = bps
            if len(per_horizon) == len(HORIZONS):
                for horizon, bps in per_horizon.items():
                    balanced[horizon].append(bps)

        print(f"=== {symbol} ===   firings={len(firings):,}  unresolved={unresolved:,}")
        print("\nALL firings — n shrinks with horizon, so rows are NOT comparable:")
        _table(returns)
        print("\nSAME firings at every horizon (only those with a full runway) — comparable:")
        _table(balanced)
        print()

    print("|t| > 2 is the conventional bar for 'probably not chance'. A mean near zero with")
    print("|t| < 2 is what a rule with NO directional edge looks like, and is the honest")
    print("outcome to expect — the barrier sweep already showed every option-buying")
    print("geometry losing. Read the hit rate beside the mean: a mean carried by a few")
    print("large moves is a different rule from one that is right slightly more often.")
    return 0


def _table(series_by_horizon: dict[int, list[float]]) -> None:
    print(f"{'horizon':>9}{'n':>8}{'mean bps':>11}{'median':>9}{'t-stat':>9}{'hit rate':>10}")
    for horizon in HORIZONS:
        series = series_by_horizon.get(horizon, [])
        if len(series) < 30:
            print(f"{horizon:>8}m{len(series):>8,}   (too few to report)")
            continue
        mean = statistics.fmean(series)
        median = statistics.median(series)
        stdev = statistics.stdev(series)
        t_stat = mean / (stdev / math.sqrt(len(series))) if stdev > 0 else 0.0
        hits = sum(1 for r in series if r > 0) / len(series)
        print(f"{horizon:>8}m{len(series):>8,}{mean:>+11.2f}{median:>+9.2f}{t_stat:>+9.2f}{hits:>9.1%}")


def _direction_for(session_factory, evaluation_id: str) -> str | None:  # noqa: ANN001
    """Recovers `long_call`/`long_put` from the recorded breakout condition.

    The direction is not a column — it lives in the human-readable condition
    text the rule emitted, which `te.ml.labeling.parse_breakout` already
    knows how to read. Reusing that parser rather than writing a second one
    keeps exactly one definition of what a firing's direction was."""
    from te.persistence.models import EvaluationConditionRow

    with session_factory() as session:
        conditions = (
            session.query(EvaluationConditionRow)
            .filter(EvaluationConditionRow.evaluation_id == evaluation_id)
            .order_by(EvaluationConditionRow.seq)
            .all()
        )
    for condition in conditions:
        # `direction_from_breakout` takes the RAW recorded string, not a
        # parsed tuple — it is the single place that decides what a close
        # above/below the range means, and duplicating that here would be a
        # second definition of direction.
        direction = direction_from_breakout(condition.actual)
        if direction is not None:
            return direction
    return None


def _close_at(store: BarStore, symbol: str, at: dt.datetime) -> float | None:
    """The index close at or just before `at`, via the same point-in-time
    gate every other reader uses — never a forward-looking peek."""
    visible = bars_asof(store, symbol, at, dt.timedelta(minutes=10), interval="1m")
    if visible.empty:
        return None
    return float(visible.iloc[-1]["c"])


if __name__ == "__main__":
    raise SystemExit(main())
