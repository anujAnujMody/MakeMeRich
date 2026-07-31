#!/usr/bin/env python
"""Triple-barrier-labels every recorded ORB firing and reports the outcome mix.

Usage:
    python scripts/label_replay_firings.py
    python scripts/label_replay_firings.py --instruments NIFTY,BANKNIFTY

### The approximation, stated up front

Barriers are walked on the **INDEX** path, not on the option premium, and
that is a real limitation rather than a preference. Verified against the
live OpenAlgo instance on 2026-07-31: `history` on an expired option symbol
returns `Symbol 'NIFTY30SEP2524500CE' not found` — an option's candles
disappear with the contract. The only historical option data available is
the daily bhavcopy OHLC in `option_bhav`, which cannot say what a contract
was worth at 10:47, nor whether its high came before its low. Index
1-minute bars, by contrast, are complete for the whole period.

So a premium barrier is converted into an index barrier through delta:

    index_move = (pct x ATM_premium) / delta

`ATM_PARAMS` below holds the real ATM figures measured from the live chain
on 2026-07-31 (`get_option_greeks`, risk-free 5.5%). Two honest caveats
that the resulting labels inherit:

* **One IV snapshot stands in for eleven months.** Delta and premium move
  with implied volatility; these were taken on a single day. A high-VIX day
  in the sample really needed a wider index barrier than this assigns.
* **Theta is ignored.** Over a ~3h hold, theta on a 4-DTE NIFTY ATM is
  roughly 1.5% of premium against a 20% stop — second-order next to delta,
  but it makes these labels very slightly optimistic.

The per-instrument spread in that table is itself a finding, not noise:
BANKNIFTY and BANKEX have **no weekly expiry** (nearest is monthly), so
their ATM options are 25-27 DTE with far lower gamma. The same "+40% on the
premium" needs a 1.11% index move on BANKNIFTY versus 0.30% on NIFTY. A
single shared barrier would have quietly labelled four different strategies
as one.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from decimal import Decimal

from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.domain.costs import CostModel, select_rates
from te.domain.money import Paise
from te.ml.barriers import (
    ATM_SNAPSHOTS,
    barrier_pct_as_index_pct,
    index_barriers,
    round_trip_cost_in_index_points,
)
from te.ml.labeling import label_firings_from_evaluations
from te.persistence.db import make_engine, make_session_factory
from te.settings import Settings

#: Must track `Settings.paper_cycle_stop_pct` / `paper_cycle_target_pct`.
#: 1:1 since the barrier sweep — labelling at a geometry the engine no longer
#: trades would describe a different strategy, and any runway or filter
#: derived from those labels would be tuned for a configuration that is not
#: running. See `scripts/sweep_barriers.py` for the evidence.
STOP_PCT = Decimal(20)
TARGET_PCT = Decimal(20)
MAX_HOLD = dt.timedelta(hours=3)

#: Earliest firing that can be labelled with rates we actually hold.
#: `config/charges.yaml` carries ONE rate row, `effective_from: 2026-04-01`
#: (the day STT rose 0.10% -> 0.15%), and `CostModel` refuses to price a
#: trade that predates it. Labelling the earlier ~4,000 firings is not a code
#: change: it needs a VERIFIED pre-April rate row added to that file.
RATES_VERIFIED_FROM = dt.date(2026, 4, 1)


def barriers(symbol: str) -> tuple[Paise, Paise]:
    return index_barriers(symbol, stop_pct=STOP_PCT, target_pct=TARGET_PCT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instruments", default=",".join(ATM_SNAPSHOTS))
    parser.add_argument("--strategy", default="orb")
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

    print(f"barriers: stop -{STOP_PCT}% / target +{TARGET_PCT}% of ATM premium, max hold {MAX_HOLD}")
    print(f"labelling firings from {RATES_VERIFIED_FROM} onward (charge rates verified from that date)\n")
    print(f"{'instrument':<11}{'stop pts':>10}{'target pts':>12}{'stop %':>9}{'target %':>10}{'DTE':>6}")
    for symbol in chosen:
        stop, target = barriers(symbol)
        print(
            f"{symbol:<11}{int(stop) / 100:>10.1f}{int(target) / 100:>12.1f}"
            f"{barrier_pct_as_index_pct(symbol, STOP_PCT):>8.3f}%"
            f"{barrier_pct_as_index_pct(symbol, TARGET_PCT):>9.3f}%"
            f"{ATM_SNAPSHOTS[symbol].days_to_expiry:>6.1f}"
        )
    print()

    grand: Counter[str] = Counter()
    for symbol in chosen:
        stop, target = barriers(symbol)
        firings = label_firings_from_evaluations(
            session_factory,
            store,
            cost_model,
            strategy=args.strategy,
            exchange=ATM_SNAPSHOTS[symbol].exchange,
            stop_distance=stop,
            target_distance=target,
            max_hold=MAX_HOLD,
            instrument=symbol,
            since=RATES_VERIFIED_FROM,
            cost_per_unit=round_trip_cost_in_index_points(symbol, cost_model, RATES_VERIFIED_FROM),
        )
        counts = Counter(f.barrier for f in firings)
        grand.update(counts)
        n = len(firings)
        if not n:
            print(f"{symbol:<11} no firings")
            continue
        wins = counts.get("target", 0)
        decided = wins + counts.get("stop", 0)
        win_rate = f"{wins / decided:.1%}" if decided else "n/a"
        print(
            f"{symbol:<11} n={n:<6,} target={counts.get('target', 0):<6,} "
            f"stop={counts.get('stop', 0):<6,} time={counts.get('time', 0):<6,} "
            f"win-rate(decided)={win_rate}"
        )

    total = sum(grand.values())
    if total:
        decided = grand.get("target", 0) + grand.get("stop", 0)
        print(f"\nTOTAL n={total:,}  target={grand.get('target', 0):,}  stop={grand.get('stop', 0):,}  "
              f"time={grand.get('time', 0):,}")
        if decided:
            print(f"win rate on decided firings: {grand.get('target', 0) / decided:.1%}")
        print("\nNOTE: index-path labels, delta-converted from a single-day IV snapshot, theta ignored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
