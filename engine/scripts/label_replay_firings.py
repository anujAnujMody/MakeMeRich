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
import sys
from collections import Counter
from functools import partial

from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.lot_size_history import UnknownLotSizeError, load_lot_size_history, lot_size_on
from te.data.option_history import OptionContractIndex
from te.domain.costs import CostModel
from te.engine.scheduler import _exit_geometry
from te.ml.barriers import (
    ATM_SNAPSHOTS,
    MAX_HOLD,
    RATES_VERIFIED_FROM,
    STOP_PCT,
    TARGET_PCT,
    barrier_pct_as_index_pct,
    barriers,
    round_trip_cost_in_index_points,
)
from te.ml.labeling import RealPremiumConfig, label_firings_from_evaluations
from te.persistence.db import make_engine, make_session_factory
from te.settings import Settings

#: Re-exported so the four sibling scripts that already import these from
#: here keep working; the definitions moved into `te.ml.barriers` on
#: 2026-08-05 so the scheduled training job can reach them without a package
#: module importing from `scripts/`. See that module's docstring.
__all__ = ["MAX_HOLD", "RATES_VERIFIED_FROM", "STOP_PCT", "TARGET_PCT", "barriers"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instruments", default=",".join(ATM_SNAPSHOTS))
    parser.add_argument("--strategy", default="orb")
    parser.add_argument(
        "--real-premiums",
        action="store_true",
        help=(
            "walk each firing's REAL option contract instead of converting index points through "
            "te.ml.barriers' single delta snapshot. Only possible where the option archive covers "
            "the firing (NIFTY, 2024-01-04..2026-05-05); anything else falls back and is reported "
            "separately, never silently mixed."
        ),
    )
    args = parser.parse_args()

    chosen = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in ATM_SNAPSHOTS]
    if unknown:
        print(f"no measured ATM params for: {', '.join(unknown)}", file=sys.stderr)
        return 2

    settings = Settings()
    store = BarStore(settings.bar_store_path)
    session_factory = make_session_factory(make_engine(settings.database_url))
    # The WHOLE table, not today's row: this script walks years of firings
    # and each must be priced at the rates that were really in force on its
    # own date. See `CostModel.__init__`.
    cost_model = CostModel(load_charge_rate_table(settings.charges_path))
    # The geometry the LIVE engine actually trades, from the one selector
    # that decides it (`te.engine.scheduler._exit_geometry`) — never a second
    # copy of its branching. `STOP_PCT`/`TARGET_PCT` below are the OLD,
    # no-longer-live 20%/20% figures, kept only as a labelled display value.
    geometry = _exit_geometry(settings)
    max_lots = settings.paper_cycle_max_lots or 1

    print(
        f"barriers: stop -{STOP_PCT}% / target +{TARGET_PCT}% of ATM premium "
        f"(HISTORICAL, no longer live), max hold {MAX_HOLD}"
    )
    print(f"live geometry actually used below: {geometry!r}, max_lots={max_lots}")
    print(f"labelling firings from {RATES_VERIFIED_FROM} onward (charge rates verified from that date)\n")
    print(f"{'instrument':<11}{'stop pts':>10}{'target pts':>12}{'stop %':>9}{'target %':>10}{'DTE':>6}")
    for symbol in chosen:
        stop, target = barriers(symbol, geometry=geometry, max_lots=max_lots)
        print(
            f"{symbol:<11}{int(stop) / 100:>10.1f}{int(target) / 100:>12.1f}"
            f"{barrier_pct_as_index_pct(symbol, STOP_PCT):>8.3f}%"
            f"{barrier_pct_as_index_pct(symbol, TARGET_PCT):>9.3f}%"
            f"{ATM_SNAPSHOTS[symbol].days_to_expiry:>6.1f}"
        )
    print()

    # Built once per symbol and only when asked for: scanning the store's
    # directory listing for 24k option symbols is cheap but not free, and an
    # index-path run must not pay for it.
    real_premiums: dict[str, RealPremiumConfig] = {}
    if args.real_premiums:
        lot_history = load_lot_size_history(settings.charges_path.parent / "lot_sizes.yaml")
        for symbol in chosen:
            # Resolved PER FIRING, not once for the run. NIFTY's lot size
            # changed twice inside the archive's span, and lot size sets how
            # far the flat Rs 20-per-order brokerage is spread — so a single
            # value shifts cost-per-unit by more than 2x at the extremes,
            # moving the cost-adjusted target and the win rate with it.
            try:
                lot_size_on(lot_history, symbol, RATES_VERIFIED_FROM)  # fail fast if unknown
            except UnknownLotSizeError as exc:
                print(f"{symbol}: {exc}", file=sys.stderr)
                return 2
            real_premiums[symbol] = RealPremiumConfig(
                contracts=OptionContractIndex(store, symbol),
                stop_pct=STOP_PCT,
                target_pct=TARGET_PCT,
                lot_size_for=partial(lot_size_on, lot_history, symbol),
            )
            sizes = sorted({r.lot_size for r in lot_history[symbol]})
            print(f"{symbol}: real-premium path enabled, lot sizes across the span: {sizes}")
        print()

    grand: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    for symbol in chosen:
        stop, target = barriers(symbol, geometry=geometry, max_lots=max_lots)
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
            real_premiums=real_premiums.get(symbol),
        )
        counts = Counter(f.barrier for f in firings)
        grand.update(counts)
        sources.update(f.source for f in firings)
        # Per-symbol too: an instrument whose archive coverage is thin can
        # be almost entirely fallback while the TOTAL still looks mostly
        # real. Reporting only the total is how a mixed dataset passes for a
        # clean one.
        by_source = Counter(f.source for f in firings)
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
        real = by_source.get("real_option", 0)
        print(
            f"{'':<11} labels: real option premiums={real:,} "
            f"({real / n:.1%})  index approximation={by_source.get('index_approx', 0):,}"
        )

    total = sum(grand.values())
    if total:
        decided = grand.get("target", 0) + grand.get("stop", 0)
        print(f"\nTOTAL n={total:,}  target={grand.get('target', 0):,}  stop={grand.get('stop', 0):,}  "
              f"time={grand.get('time', 0):,}")
        if decided:
            print(f"win rate on decided firings: {grand.get('target', 0) / decided:.1%}")
        # DERIVED from what actually happened, never asserted. This line
        # read "index-path labels ... theta ignored" unconditionally until
        # 2026-08-01 — and printed exactly that under a run that had just
        # labelled 99.7% of its firings from real option premiums. A
        # hardcoded provenance note is worse than none: it is a claim about
        # where the numbers came from, made without looking at them.
        real = sources.get("real_option", 0)
        approx = sources.get("index_approx", 0)
        if approx == 0:
            print("\nAll labels walked REAL option premiums — theta and IV moves are priced in.")
        elif real == 0:
            print(
                "\nNOTE: every label came from the INDEX approximation — delta-converted from a "
                "single-day IV snapshot with theta ignored, so these outcomes are optimistically "
                "biased in a way no number here reveals."
            )
        else:
            print(
                f"\nMIXED: {real:,} labels ({real / (real + approx):.1%}) walked real option "
                f"premiums; {approx:,} fell back to the index approximation, which is "
                "optimistically biased. A combined win rate over both is not one measurement."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
