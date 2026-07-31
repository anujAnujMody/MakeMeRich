#!/usr/bin/env python
"""Does opening-range WIDTH separate ORB winners from losers?

Usage:
    python scripts/analyze_range_width.py
    python scripts/analyze_range_width.py --instruments NIFTY,SENSEX

ORB currently has no working entry filter. The volume-confirmation condition
never ran (an index reports `volume=0`, so it compared `0 >= 0` and passed
every time), and the labelled firings say the raw rule is negative
expectancy: 210 targets against 839 stops on a 2:1 barrier, where breakeven
needs 33.3%.

Range width is the filter the strategy literature converges on — "skip the
day if the opening range is too narrow" (chop, no trend to break out of) or
"too wide" (the move already happened). This script measures whether that
holds in OUR data instead of adopting someone's number.

### This is IN-SAMPLE and cannot justify a threshold on its own

Every firing here is one the rule already fired on, over the same period any
threshold would then be evaluated on. Picking the best-looking bucket and
adopting its edge is the exact overfitting this project's `TrialLedger`,
Deflated Sharpe Ratio and PBO machinery exist to catch — a search over
buckets is a search, and it inflates the honest trial count whether or not
anyone writes it down.

Read the output as "is there a signal worth testing properly?", never as
"set the threshold here". A real threshold has to survive
`te.ml.cv`'s purged CV on data this search never saw.

Range width is `range_high - range_low` from ORB's own recorded condition
string, as a percentage of the breakout close — so it is what the rule
actually observed, not a re-derivation from bars.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import defaultdict

from sqlalchemy import select

from scripts.label_replay_firings import MAX_HOLD, RATES_VERIFIED_FROM, barriers
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.domain.costs import CostModel, select_rates
from te.ml.barriers import ATM_SNAPSHOTS, round_trip_cost_in_index_points
from te.ml.labeling import label_firings_from_evaluations, parse_breakout
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import EvaluationConditionRow
from te.settings import Settings

_BREAKOUT_LABEL = "breakout close beyond opening range"

#: Barriers are 2:1 (target 2x stop), so a firing must win more than
#: 1/(1+2) of the time just to break even before costs.
BREAKEVEN_WIN_RATE = 1 / 3


def range_width_pct(actual: str) -> float | None:
    """Opening-range width as a percentage of the breakout close, parsed via
    `te.ml.labeling.parse_breakout` rather than a second copy of the regex —
    ORB's `actual` format has exactly one parser."""
    parsed = parse_breakout(actual)
    if parsed is None:
        return None
    close, low, high = (float(v) for v in parsed)
    if close <= 0:
        return None
    return (high - low) / close * 100


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instruments", default=",".join(ATM_SNAPSHOTS))
    parser.add_argument("--buckets", type=int, default=5)
    args = parser.parse_args()

    chosen = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in ATM_SNAPSHOTS]
    if unknown:
        print(f"no measured ATM params for: {', '.join(unknown)}", file=sys.stderr)
        return 2

    settings = Settings()
    store = BarStore(settings.bar_store_path)
    session_factory = make_session_factory(make_engine(settings.database_url))
    cost_model = CostModel(select_rates(load_charge_rate_table(settings.charges_path), dt.date.today()))

    # evaluation_id -> range width %, straight from what ORB recorded.
    with session_factory() as session:
        widths = {
            row.evaluation_id: range_width_pct(row.actual)
            for row in session.execute(
                select(EvaluationConditionRow).where(EvaluationConditionRow.label == _BREAKOUT_LABEL)
            )
            .scalars()
            .all()
        }

    print("IN-SAMPLE exploratory analysis — see this script's docstring.")
    print(f"breakeven win rate on these 2:1 barriers: {BREAKEVEN_WIN_RATE:.1%}\n")

    for symbol in chosen:
        stop, target = barriers(symbol)
        firings = label_firings_from_evaluations(
            session_factory,
            store,
            cost_model,
            strategy="orb",
            exchange=ATM_SNAPSHOTS[symbol].exchange,
            stop_distance=stop,
            target_distance=target,
            max_hold=MAX_HOLD,
            instrument=symbol,
            since=RATES_VERIFIED_FROM,
            cost_per_unit=round_trip_cost_in_index_points(symbol, cost_model, RATES_VERIFIED_FROM),
        )
        # No instrument filter needed: `label_firings_from_evaluations`
        # already restricted the query to `symbol`.
        rows: list[tuple[float, str]] = []
        for firing in firings:
            width = widths.get(firing.evaluation_id)
            if width is not None:
                rows.append((width, firing.barrier))
        if len(rows) < args.buckets * 10:
            print(f"{symbol}: only {len(rows)} usable firings — too few to bucket\n")
            continue

        rows.sort(key=lambda r: r[0])
        per = len(rows) // args.buckets
        print(f"=== {symbol} ===  n={len(rows):,}")
        print(f"{'range width %':>18}{'n':>7}{'target':>8}{'stop':>7}{'time':>7}{'win(decided)':>14}")
        for b in range(args.buckets):
            lo_i = b * per
            hi_i = (b + 1) * per if b < args.buckets - 1 else len(rows)
            chunk = rows[lo_i:hi_i]
            counts: defaultdict[str, int] = defaultdict(int)
            for _, barrier in chunk:
                counts[barrier] += 1
            decided = counts["target"] + counts["stop"]
            wr = counts["target"] / decided if decided else 0.0
            flag = "  <-- above breakeven" if decided and wr > BREAKEVEN_WIN_RATE else ""
            band = f"{chunk[0][0]:.2f}-{chunk[-1][0]:.2f}"
            print(
                f"{band:>18}{len(chunk):>7}{counts['target']:>8}{counts['stop']:>7}"
                f"{counts['time']:>7}{wr:>13.1%}{flag}"
            )
        print()

    print("A bucket above breakeven here is a HYPOTHESIS, not a threshold — it was")
    print("selected on the same data it is scored on. Validate with purged CV before use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
