#!/usr/bin/env python
"""Sweeps the STRUCTURE of a credit spread — strike distance, width, days to
expiry, profit target, stop, hold time — one dimension at a time.

Usage:
    python -m scripts.sweep_spread_structure
    python -m scripts.sweep_spread_structure --strategies random_entry

### Why structure and not more strategies

Thirty-two entry strategies were tested on both bought options and credit
spreads. In both, the random-entry control landed MID-TABLE — 13th of 31
buying, 12th of 31 on spreads. Twelve strategies beat a coin flip, nineteen
lost to it. Entry timing is not what decides the outcome at these costs.

What DID move results was structure:

  * buying -> credit spread: loss shrank about 4x (-0.06 -> -0.015 R)
  * at-the-money -> 8 strikes out: loss grew about 7x (-0.062 -> -0.414 R)

Both are far larger than any difference between strategies. So this sweeps
the structure and holds the strategy fixed, rather than the other way round.

### Why random entry is the primary probe

`random_entry` is the cleanest instrument for measuring a WRAPPER, precisely
because it contributes no signal of its own. Whatever it earns or loses is
the structure's own economics, with nothing from timing mixed in.

That also makes it the sharpest possible test of the "selling collects a
risk premium" claim: if some structure makes even a coin flip profitable,
the structure itself has positive expectancy and the entry rule is a bonus.
If no structure does, then spreads need a real signal — and we do not have
one.

Real strategies are run alongside to check whether any signal adds anything
on top of whatever the structure does.

### One dimension at a time, deliberately

A full grid over six dimensions is thousands of combinations, and the best
of thousands of pure-noise cells looks spectacular by construction — the
exact trap this project has caught itself in twice already. Sweeping one
dimension at a time is a few dozen trials, and it answers a better question:
not "which cell won" but "does this lever have a consistent, explainable
effect".

A monotonic pattern across a whole dimension (as the strike-distance test
produced) is far stronger evidence than one cell scoring well, because a
single lucky cell cannot fake a trend across six ordered values.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import replace
from decimal import Decimal
from functools import partial

import sqlalchemy as sa

from te.backtest.spread_lab import (
    SpreadGeometry,
    _score,
    collect_signals,
    label_signals,
)
from te.backtest.strategy_lab import _WholeSymbolCache
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.lot_size_history import load_lot_size_history, lot_size_on
from te.data.option_history import OptionContractIndex
from te.domain.costs import CostModel
from te.ml.metrics import expected_max_z
from te.ml.trials import TrialLedger
from te.settings import Settings
from te.strategy.registry import available

#: The centre of the sweep. Every dimension is varied around THIS, holding
#: the others fixed — so each row is comparable to the baseline and to its
#: own neighbours.
BASELINE = SpreadGeometry(
    short_otm=2,
    width_strikes=3,
    profit_target_pct=Decimal("0.5"),
    stop_loss_multiple=Decimal("2.0"),
    max_hold=dt.timedelta(minutes=180),
    min_days_to_expiry=0,
    max_days_to_expiry=7,
)


def _dimensions() -> dict[str, list[tuple[str, SpreadGeometry]]]:
    """`dimension -> [(label, geometry)]`, each varying ONE field."""
    return {
        "strike distance (how far the sold leg sits from spot)": [
            (f"otm {n}", replace(BASELINE, short_otm=n)) for n in (0, 1, 2, 3, 5, 8)
        ],
        "width (how far the protective leg sits beyond it)": [
            (f"width {n}", replace(BASELINE, width_strikes=n)) for n in (1, 2, 3, 5, 10)
        ],
        "days to expiry at entry": [
            ("0 (expiry day)", replace(BASELINE, min_days_to_expiry=0, max_days_to_expiry=0)),
            ("1", replace(BASELINE, min_days_to_expiry=1, max_days_to_expiry=1)),
            ("2", replace(BASELINE, min_days_to_expiry=2, max_days_to_expiry=2)),
            ("3-4", replace(BASELINE, min_days_to_expiry=3, max_days_to_expiry=4)),
            ("5-7", replace(BASELINE, min_days_to_expiry=5, max_days_to_expiry=7)),
        ],
        "profit target (fraction of credit captured before closing)": [
            (f"take {pct}", replace(BASELINE, profit_target_pct=Decimal(pct)))
            for pct in ("0.25", "0.5", "0.75", "0.95")
        ],
        "stop loss (multiple of credit at which to bail)": [
            (f"stop {mult}x", replace(BASELINE, stop_loss_multiple=Decimal(mult)))
            for mult in ("1.5", "2.0", "3.0", "10.0")
        ],
        "hold time": [
            (f"{m}m", replace(BASELINE, max_hold=dt.timedelta(minutes=m))) for m in (30, 60, 180, 375)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instrument", default="NIFTY")
    parser.add_argument("--exchange", default="NFO")
    parser.add_argument(
        "--strategies",
        default="random_entry,orb60,donchian_20",
        help="random_entry is the structure probe; the others check whether any signal adds to it",
    )
    parser.add_argument("--no-ledger", action="store_true", help="smoke tests ONLY — scores become too generous")
    parser.add_argument("--run-id", default="spread-structure-sweep")
    args = parser.parse_args()

    chosen = [s.strip() for s in args.strategies.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in available()]
    if unknown:
        print(f"unknown strategies: {', '.join(unknown)}", file=sys.stderr)
        return 2

    settings = Settings()
    store = BarStore(settings.bar_store_path)
    cached_store = _WholeSymbolCache(store.root, store)
    cost_model = CostModel(load_charge_rate_table(settings.charges_path))
    lot_history = load_lot_size_history(settings.charges_path.parent / "lot_sizes.yaml")
    contracts = OptionContractIndex(store, args.instrument)
    ledger = None if args.no_ledger else TrialLedger(sa.create_engine(settings.database_url))

    dimensions = _dimensions()
    total_trials = sum(len(v) for v in dimensions.values()) * len(chosen)
    floor = expected_max_z(total_trials)

    print(f"Spread STRUCTURE sweep on {args.instrument} — one dimension at a time.")
    print(f"baseline: {BASELINE}")
    print(f"strategies: {', '.join(chosen)}  (random_entry is the pure-structure probe)")
    print(f"{total_trials} configurations -> a result must clear |t| > {floor:.2f} to beat the noise floor.\n")
    if ledger is None:
        print("WARNING: --no-ledger — deflated scores are TOO GENEROUS and must not be reported.\n")

    # Strategies are evaluated ONCE; every geometry re-labels the same
    # signals. Sweeping ~28 geometries the naive way would be ~28 full
    # passes over 600 sessions.
    print("collecting signals (one pass over history)...", flush=True)
    signals = collect_signals(
        strategy_names=chosen, store=store, contracts=contracts,
        lot_size_for=partial(lot_size_on, lot_history, args.instrument), instrument=args.instrument,
    )
    for name in chosen:
        print(f"  {name:<20} {len(signals[name]):,} signals")
    print()

    for dimension, variants in dimensions.items():
        print(f"=== {dimension} ===")
        print(f"{'setting':<18}{'strategy':<16}{'trades':>8}{'win %':>8}{'mean R':>9}{'t':>8}{'margin/lot':>12}")
        for label, geometry in variants:
            for name in chosen:
                trades, unresolved = label_signals(
                    signals=signals[name], store=cached_store, contracts=contracts,
                    cost_model=cost_model, geometry=geometry, exchange=args.exchange,
                )
                result = _score(
                    strategy=name, instrument=args.instrument, trades=trades, unresolved=unresolved,
                    first_day=None, last_day=None, trial_ledger=ledger, run_id=args.run_id,
                )
                if result.trades < 30:
                    print(f"{label:<18}{name:<16}{result.trades:>8,}   too few trades")
                    continue
                margin = int(result.median_margin_paise) / 100
                print(
                    f"{label:<18}{name:<16}{result.trades:>8,}{result.win_rate:>7.1%}"
                    f"{result.mean_r:>+9.3f}{result.t_stat:>+8.2f}{margin:>12,.0f}"
                )
        print()

    print("How to read this:")
    print("  Look for a CONSISTENT TREND across a dimension, not the single best row — one lucky cell")
    print("  cannot fake a trend across several ordered values, but it can easily top a table.")
    print("  Compare every row against random_entry on the SAME setting: that difference is what the")
    print("  entry signal contributes. If they move together, the structure is doing all the work.")
    print("  mean R is in units of the spread's MAXIMUM POSSIBLE LOSS, net of real costs on all four legs.")
    print("  'margin/lot' is the real capital one lot needs — a great result at 40,000/lot is not")
    print("  tradeable on this account, so read it alongside every other number.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
