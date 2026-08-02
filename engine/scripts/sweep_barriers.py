#!/usr/bin/env python
"""Is ORB losing because the RULE is bad, or because the BARRIERS are?

Usage:
    python -m scripts.sweep_barriers
    python -m scripts.sweep_barriers --instruments NIFTY,SENSEX

The labelled firings say the rule is negative expectancy: 210 targets
against 839 stops, a 20.0% win rate where the 2:1 barrier needs 33.3% to
break even. But that "-0.15R" is not a fact about ORB. It is a fact about
ORB **with a 20% stop and a 40% target** — and that geometry was chosen,
not measured. The only evidence behind it is a Zerodha study that specified
the STOP alone; the 40% target has nothing behind it at all.

Two very different diagnoses produce the same 20%:

1. the rule has no edge, and no barrier choice will rescue it; or
2. the target is simply too far — a +40% move on a 4-DTE option inside a
   3-hour hold is a demanding ask — and a nearer target converts many of
   those 1,525 time-exits into wins.

This sweep separates them, which is cheap (the labeller already takes the
barriers as parameters) and worth doing BEFORE training a model, since a
model trained on labels from an arbitrary barrier choice is fitted to that
choice as much as to the market.

### Reading the output

`breakeven` is `1 / (1 + reward/risk)` — the win rate needed on DECIDED
firings just to break even before costs. `expectancy` is per firing in R
(units of the stop distance), counting time exits as 0:

    E = P(target) x (reward/risk) - P(stop) x 1

Treating time exits as neutral is an approximation, and an optimistic one
for a decaying option: a position closed on the clock has usually bled
theta. So a marginally positive expectancy here is NOT a green light.

### This is a search, and searches overfit

Every combination scored below is scored on the same firings. Picking the
best row and adopting it is precisely what `TrialLedger`/DSR/PBO exist to
catch. Read the output for SHAPE — does expectancy improve monotonically as
the target comes in? — not for a winner to copy.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from decimal import Decimal
from functools import partial

from scripts.label_replay_firings import MAX_HOLD, RATES_VERIFIED_FROM
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.lot_size_history import UnknownLotSizeError, load_lot_size_history, lot_size_on
from te.data.option_history import OptionContractIndex
from te.domain.costs import CostModel
from te.ml.barriers import ATM_SNAPSHOTS, index_barriers, round_trip_cost_in_index_points
from te.ml.labeling import RealPremiumConfig, label_firings_from_evaluations
from te.persistence.db import make_engine, make_session_factory
from te.settings import Settings

#: (stop %, target %) of ATM premium. Includes the shipped 20/40 so the
#: current setting is directly comparable, and holds the STOP fixed at 20%
#: across most rows — the stop is the one number with published support, so
#: the sweep varies what is unevidenced rather than everything at once.
COMBOS: tuple[tuple[float, float], ...] = (
    (20.0, 20.0),   # 1:1
    (20.0, 30.0),   # 1:1.5
    (20.0, 40.0),   # 1:2  <- current setting
    (20.0, 60.0),   # 1:3
    (15.0, 22.5),   # 1:1.5, tighter stop
    (30.0, 45.0),   # 1:1.5, wider stop
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instruments", default="NIFTY,SENSEX")
    parser.add_argument("--strategy", default="orb", help="firing set to label, e.g. orb60")
    parser.add_argument(
        "--real-premiums",
        action="store_true",
        help=(
            "walk each firing's REAL option contract instead of converting index points through "
            "te.ml.barriers' single delta snapshot. Without this the sweep is measuring a "
            "premium path with no theta in it, which is what made the 1:1 row look break-even."
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
    # The WHOLE table: this sweep spans years and each firing must be priced
    # at the rates in force on its own date.
    cost_model = CostModel(load_charge_rate_table(settings.charges_path))

    real_premiums: dict[str, object] = {}
    if args.real_premiums:
        lot_history = load_lot_size_history(settings.charges_path.parent / "lot_sizes.yaml")
        for symbol in chosen:
            try:
                lot_size_on(lot_history, symbol, RATES_VERIFIED_FROM)  # fail fast if unknown
            except UnknownLotSizeError as exc:
                print(f"{symbol}: {exc}", file=sys.stderr)
                return 2
            real_premiums[symbol] = OptionContractIndex(store, symbol)

    print("IN-SAMPLE barrier sweep — read for SHAPE, not for a winner to copy.")
    print("expectancy in R (stop units), time exits counted as 0 (optimistic for a decaying option).\n")

    for symbol in chosen:
        cost = round_trip_cost_in_index_points(symbol, cost_model, RATES_VERIFIED_FROM)
        print(f"=== {symbol} ===")
        print(f"{'stop%':>6}{'tgt%':>6}{'R:R':>6}{'n':>7}{'target':>8}{'stop':>7}{'time':>7}"
              f"{'win':>7}{'breakeven':>11}{'expectancy':>12}{'real':>8}")
        for stop_pct, target_pct in COMBOS:
            stop, target = index_barriers(
                symbol, stop_pct=Decimal(str(stop_pct)), target_pct=Decimal(str(target_pct))
            )
            contracts = real_premiums.get(symbol)
            config = (
                RealPremiumConfig(
                    contracts=contracts,  # type: ignore[arg-type]
                    stop_pct=Decimal(str(stop_pct)),
                    target_pct=Decimal(str(target_pct)),
                    lot_size_for=partial(lot_size_on, lot_history, symbol),
                )
                if contracts is not None
                else None
            )
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
                cost_per_unit=cost,
                real_premiums=config,
            )
            counts = Counter(f.barrier for f in firings)
            sources = Counter(f.source for f in firings)
            n = len(firings)
            if not n:
                continue
            rr = target_pct / stop_pct
            decided = counts["target"] + counts["stop"]
            win = counts["target"] / decided if decided else 0.0
            breakeven = 1 / (1 + rr)
            expectancy = (counts["target"] / n) * rr - (counts["stop"] / n)
            marker = "  <--" if expectancy > 0 else ""
            current = " (current)" if (stop_pct, target_pct) == (20.0, 40.0) else ""
            # Provenance on EVERY row, not once at the bottom. A row whose
            # contracts are thinly covered can silently be mostly index
            # approximation while the run as a whole looks real, and an
            # approximated row is optimistically biased against a real one
            # sitting directly above it in the same table.
            real_pct = sources.get("real_option", 0) / n
            print(
                f"{stop_pct:>6.1f}{target_pct:>6.1f}{rr:>6.2f}{n:>7,}{counts['target']:>8,}"
                f"{counts['stop']:>7,}{counts['time']:>7,}{win:>6.1%}{breakeven:>10.1%}"
                f"{expectancy:>+11.3f}R{real_pct:>8.0%}{marker}{current}"
            )
        print()

    print("A positive expectancy here is a HYPOTHESIS. It was selected on the same")
    print("firings it is scored on, and time exits were counted as costless.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
