#!/usr/bin/env python
"""Does the OPENING RANGE LENGTH matter, and is 15 minutes the right one?

Usage:
    python -m scripts.sweep_range_length
    python -m scripts.sweep_range_length --instruments NIFTY --lengths 5,15,30

15 minutes is the number this engine has always used, and the research says
plainly that it is the most-COPIED default rather than a tested one: sources
split about evenly between 5, 15 and 30 with no controlled comparison on
Indian index options, and the one rigorous study found (Zarattini et al., US
single stocks) uses 5 minutes and attributes its edge to a short range plus a
volume filter — a combination unavailable here, since an index reports no
volume of its own.

So this measures it on OUR data. Each length is a full replay: the rule
re-evaluates every recorded minute with that opening range, producing a
different firing set, which is then labelled at the current 1:1 barriers and
scored the same way `sweep_barriers.py` scores geometry.

### The trap this script exists to avoid

`replay_orb(opening_range_minutes=...)` alone does NOT reach the rule — it
only shifts the first evaluable minute. The registry builds `OrbStrategy()`
with its default 15-minute range regardless, so a sweep written the obvious
way returns identical firings at every length and reads as "range length
does not matter". A `strategy_factory` is passed for exactly this reason, and
`tests/backtest/test_replay.py` pins it.

### Read for shape, and mind the trial count

Each length is another configuration tried on the same 11 months, so this
inflates the honest trial count the same way the barrier sweep did. A length
that looks best here is a HYPOTHESIS to test on data this search never saw —
not a setting to adopt. Expectancy also counts time exits as costless, which
flatters a decaying option, and every caveat in `te.ml.barriers` about the
single-day IV snapshot applies unchanged.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter

from scripts.label_replay_firings import MAX_HOLD, RATES_VERIFIED_FROM, STOP_PCT, TARGET_PCT, barriers
from te.backtest.replay import replay_orb
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.domain.costs import CostModel, select_rates
from te.engine.contract import UNDERLYING_INDEX_EXCHANGES
from te.ml.barriers import ATM_SNAPSHOTS, round_trip_cost_in_index_points
from te.ml.labeling import label_firings_from_evaluations
from te.persistence.db import make_engine, make_session_factory
from te.settings import Settings
from te.strategy.orb import OrbParams, OrbStrategy

DEFAULT_LENGTHS = (5, 15, 30, 60)

#: Replay rows are tagged with the length that produced them, so one length's
#: firings never get labelled as another's. Without this every run after the
#: first would collide on `cycle_evaluations.evaluation_id` (UNIQUE, derived
#: from strategy+instrument+minute) and be silently skipped as "already
#: replayed" — the sweep would then score run 1's firings four times.
STRATEGY_PREFIX = "orb-or"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instruments", default="NIFTY,SENSEX")
    parser.add_argument("--lengths", default=",".join(str(n) for n in DEFAULT_LENGTHS))
    parser.add_argument("--start", default="2026-04-01", help="defaults to the charge-rate-verified window")
    parser.add_argument("--end", default="2026-07-30")
    args = parser.parse_args()

    chosen = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in ATM_SNAPSHOTS]
    if unknown:
        print(f"no measured ATM params for: {', '.join(unknown)}", file=sys.stderr)
        return 2
    lengths = [int(n) for n in args.lengths.split(",") if n.strip()]
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)

    settings = Settings()
    store = BarStore(settings.bar_store_path)
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    cost_model = CostModel(select_rates(load_charge_rate_table(settings.charges_path), dt.date.today()))

    rr = float(TARGET_PCT) / float(STOP_PCT)
    print(f"IN-SAMPLE range-length sweep, {start}..{end}, barriers {STOP_PCT}%/{TARGET_PCT}% (R:R {rr:.2f})")
    print("expectancy in R, time exits counted as 0 (optimistic for a decaying option)\n")

    for symbol in chosen:
        stop, target = barriers(symbol)
        cost = round_trip_cost_in_index_points(symbol, cost_model, RATES_VERIFIED_FROM)
        print(f"=== {symbol} ===")
        print(f"{'range':>6}{'firings':>9}{'labelled':>10}{'target':>8}{'stop':>7}{'time':>7}"
              f"{'win':>7}{'expectancy':>12}")
        for minutes in lengths:
            strategy = f"{STRATEGY_PREFIX}{minutes}"
            replayed = replay_orb(
                store=store,
                session_factory=session_factory,
                instruments={symbol: UNDERLYING_INDEX_EXCHANGES[symbol]},
                start=start,
                end=end,
                strategy_name=strategy,
                strategy_factory=lambda m=minutes: OrbStrategy(  # type: ignore[misc]
                    OrbParams(opening_range_minutes=m, min_opening_bars=3)
                ),
                opening_range_minutes=minutes,
            )
            firings = label_firings_from_evaluations(
                session_factory,
                store,
                cost_model,
                strategy=strategy,
                exchange=ATM_SNAPSHOTS[symbol].exchange,
                stop_distance=stop,
                target_distance=target,
                max_hold=MAX_HOLD,
                instrument=symbol,
                since=RATES_VERIFIED_FROM,
                cost_per_unit=cost,
            )
            counts = Counter(f.barrier for f in firings)
            n = len(firings)
            if not n:
                print(f"{minutes:>5}m{replayed.firings:>9,}{0:>10}   (nothing labelled)")
                continue
            decided = counts["target"] + counts["stop"]
            win = counts["target"] / decided if decided else 0.0
            expectancy = (counts["target"] / n) * rr - (counts["stop"] / n)
            marker = "  <--" if expectancy > 0 else ""
            print(
                f"{minutes:>5}m{replayed.firings:>9,}{n:>10,}{counts['target']:>8,}"
                f"{counts['stop']:>7,}{counts['time']:>7,}{win:>6.1%}{expectancy:>+11.3f}R{marker}"
            )
        print()

    print("Every length is another configuration tried on the same months. Read the")
    print("SHAPE — does expectancy move monotonically, and does 15m sit anywhere")
    print("special? A best-looking row is a hypothesis for purged CV, not a setting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
