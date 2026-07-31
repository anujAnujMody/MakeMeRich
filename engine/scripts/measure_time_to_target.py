#!/usr/bin/env python
"""How long does a winning ORB firing take to reach its target?

Usage:
    python -m scripts.measure_time_to_target
    python -m scripts.measure_time_to_target --instruments NIFTY,SENSEX

This exists to supply ONE number with evidence behind it:
`Settings.paper_cycle_min_minutes_before_hard_exit`, the runway a new entry
must have before the 15:20 forced exit.

The failure it prevents is concrete. On 2026-07-31 a NIFTY position opened
at 15:05 and was force-closed at 15:20 for -8.7% — Rs 6,672, or 82% of that
day's entire loss. Its stop never fired; the CLOCK closed it. Fifteen
minutes is not enough for the target to resolve, so the trade carried its
full downside while its upside was unreachable by construction.

The obvious fix is "don't enter within N minutes of the hard exit", and the
obvious temptation is to pick N by eye. Instead: measure how long winners
actually took, and set N so that a typical winner would have had room. A
firing entered with less runway than most winners need is not a trade with
poor odds — it is a trade whose good outcome is arithmetically unavailable.

Reported as percentiles of `exit_ts - entry_ts` over firings whose barrier
was `target`. The median is the natural floor: below it, more than half of
all historical winners could not have completed.

Note the survivorship framing — this measures winners ONLY, and deliberately
so. The question is not "how long does a trade last" but "how much room does
a WIN need", which is what a runway rule has to respect.
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
import sys

from scripts.label_replay_firings import (
    ATM_PARAMS,
    MAX_HOLD,
    RATES_VERIFIED_FROM,
    index_barriers,
    round_trip_cost_in_index_points,
)
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.domain.costs import CostModel, select_rates
from te.ml.labeling import label_firings_from_evaluations
from te.persistence.db import make_engine, make_session_factory
from te.settings import Settings

PERCENTILES = (10, 25, 50, 75, 90)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instruments", default="NIFTY,SENSEX")
    args = parser.parse_args()

    chosen = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in ATM_PARAMS]
    if unknown:
        print(f"no measured ATM params for: {', '.join(unknown)}", file=sys.stderr)
        return 2

    settings = Settings()
    store = BarStore(settings.bar_store_path)
    session_factory = make_session_factory(make_engine(settings.database_url))
    cost_model = CostModel(select_rates(load_charge_rate_table(settings.charges_path), dt.date.today()))

    print("minutes from entry to TARGET, over labelled winning firings\n")
    print(f"{'instrument':<12}{'winners':>9}" + "".join(f"{f'p{p}':>7}" for p in PERCENTILES))

    all_minutes: list[float] = []
    for symbol in chosen:
        stop, target, _, _ = index_barriers(symbol)
        firings = label_firings_from_evaluations(
            session_factory,
            store,
            cost_model,
            strategy="orb",
            exchange=ATM_PARAMS[symbol][4],
            stop_distance=stop,
            target_distance=target,
            max_hold=MAX_HOLD,
            instrument=symbol,
            since=RATES_VERIFIED_FROM,
            cost_per_unit=round_trip_cost_in_index_points(symbol, cost_model, RATES_VERIFIED_FROM),
        )
        minutes = sorted(
            (f.exit_ts - f.entry_ts).total_seconds() / 60 for f in firings if f.barrier == "target"
        )
        if not minutes:
            print(f"{symbol:<12}{0:>9}   (no winners to measure)")
            continue
        all_minutes.extend(minutes)
        cells = "".join(f"{_pct(minutes, p):>7.0f}" for p in PERCENTILES)
        print(f"{symbol:<12}{len(minutes):>9}{cells}")

    if all_minutes:
        all_minutes.sort()
        median = _pct(all_minutes, 50)
        p75 = _pct(all_minutes, 75)
        cells = "".join(f"{_pct(all_minutes, p):>7.0f}" for p in PERCENTILES)
        print(f"\n{'COMBINED':<12}{len(all_minutes):>9}{cells}")
        print(f"\nmedian winner takes {median:.0f} minutes; 75% complete within {p75:.0f}.")
        print(f"A runway of {median:.0f}m would have preserved half of all historical winners;")
        print(f"{p75:.0f}m preserves three quarters, at the cost of refusing more late entries.")
        print("\nThis is descriptive, not optimised — it reports what winners needed, and does")
        print("not search for the runway that maximises returns (which would need purged CV).")
    return 0


def _pct(sorted_values: list[float], pct: int) -> float:
    if not sorted_values:
        return 0.0
    return float(statistics.quantiles(sorted_values, n=100, method="inclusive")[pct - 1]) if len(
        sorted_values
    ) > 1 else sorted_values[0]


if __name__ == "__main__":
    raise SystemExit(main())
