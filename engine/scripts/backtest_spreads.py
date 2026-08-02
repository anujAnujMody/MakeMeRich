#!/usr/bin/env python
"""Backtests CREDIT SPREADS — sell one strike, buy a further one as
protection — for every registered strategy, on real premiums for both legs.

Usage:
    python -m scripts.backtest_spreads
    python -m scripts.backtest_spreads --strategies orb60,supertrend
    python -m scripts.backtest_spreads --short-otm 2 --width 3

### Why this script exists

Every strategy so far bought a single option, and none beat a coin flip —
buying pays a fixed toll every trade that swamped anything the entry rules
contributed. A credit spread flips that toll: selling makes time decay work
FOR the position, and buying a further strike as protection caps both the
risk and the real margin needed (`(width - credit) x lot size`, roughly
15-35k against the 1-2 lakh naked selling needs) — the reason this is worth
testing at all at this capital level.

Same discipline as `scripts/backtest_all_strategies.py`: every run is
recorded to the `TrialLedger` (a SEPARATE scope from single-leg backtests —
see `te.backtest.spread_lab.TRIAL_SCOPE` for why), and only the deflated
score is worth reading.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from decimal import Decimal
from functools import partial

import sqlalchemy as sa

from te.backtest.spread_lab import SpreadBacktestResult, SpreadGeometry, run_spread_backtest
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.lot_size_history import load_lot_size_history, lot_size_on
from te.data.option_history import OptionContractIndex
from te.domain.costs import CostModel
from te.ml.trials import TrialLedger
from te.settings import Settings
from te.strategy.registry import all_specs, available


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instrument", default="NIFTY")
    parser.add_argument("--exchange", default="NFO")
    parser.add_argument("--strategies", default="", help="comma-separated subset; default is every registered one")
    parser.add_argument("--short-otm", type=int, default=2, help="strikes the SOLD leg sits from at-the-money")
    parser.add_argument("--width", type=int, default=3, help="strikes the bought (protective) leg sits beyond it")
    parser.add_argument("--profit-target-pct", type=Decimal, default=Decimal("0.5"))
    parser.add_argument("--stop-loss-multiple", type=Decimal, default=Decimal("2.0"))
    parser.add_argument("--max-hold-minutes", type=int, default=180)
    parser.add_argument("--min-dte", type=int, default=0, help="earliest days-to-expiry the trade may have")
    parser.add_argument("--max-dte", type=int, default=7, help="latest days-to-expiry the trade may have")
    parser.add_argument(
        "--no-ledger",
        action="store_true",
        help="do not append to the trial ledger. For smoke tests ONLY — see backtest_all_strategies.py's own warning.",
    )
    parser.add_argument("--run-id", default="")
    parser.add_argument("--since", type=dt.date.fromisoformat, default=None, help="only count sessions from this date")
    parser.add_argument("--until", type=dt.date.fromisoformat, default=None, help="only count sessions up to this date")
    args = parser.parse_args()

    chosen = [s.strip() for s in args.strategies.split(",") if s.strip()] or available()
    unknown = [s for s in chosen if s not in available()]
    if unknown:
        print(f"unknown strategies: {', '.join(unknown)}", file=sys.stderr)
        return 2

    geometry = SpreadGeometry(
        short_otm=args.short_otm,
        width_strikes=args.width,
        profit_target_pct=args.profit_target_pct,
        stop_loss_multiple=args.stop_loss_multiple,
        max_hold=dt.timedelta(minutes=args.max_hold_minutes),
        min_days_to_expiry=args.min_dte,
        max_days_to_expiry=args.max_dte,
    )

    settings = Settings()
    store = BarStore(settings.bar_store_path)
    cost_model = CostModel(load_charge_rate_table(settings.charges_path))
    lot_history = load_lot_size_history(settings.charges_path.parent / "lot_sizes.yaml")
    contracts = OptionContractIndex(store, args.instrument)
    ledger = None if args.no_ledger else TrialLedger(sa.create_engine(settings.database_url))
    run_id = args.run_id or f"spreads-{geometry}"

    summaries = {s.name: s for s in all_specs()}
    print(f"Backtesting {len(chosen)} strategies as CREDIT SPREADS on {args.instrument}.")
    print(f"geometry: {geometry}")
    if ledger is None:
        print("WARNING: --no-ledger — deflated scores below are TOO GENEROUS and must not be reported.")
    print()

    by_name: dict[str, SpreadBacktestResult] = run_spread_backtest(
        strategy_names=chosen,
        store=store,
        contracts=contracts,
        cost_model=cost_model,
        lot_size_for=partial(lot_size_on, lot_history, args.instrument),
        geometry=geometry,
        instrument=args.instrument,
        exchange=args.exchange,
        trial_ledger=ledger,
        run_id=run_id,
        since=args.since,
        until=args.until,
    )
    results = [by_name[name] for name in chosen]
    for result in results:
        print(f"  {result.strategy:<24} trades={result.trades:<6,} unresolved={result.unresolved:,}")

    print(
        f"\n{'strategy':<24}{'family':<10}{'trades':>8}{'win %':>8}{'mean R':>9}{'t':>8}"
        f"{'luck-adj':>10}{'margin/lot':>13}  verdict"
    )
    for result in sorted(results, key=lambda r: -r.deflated):
        if result.trades < 30:
            print(f"{result.strategy:<24}{summaries[result.strategy].family:<10}{result.trades:>8,}   too few trades")
            continue
        margin_rupees = int(result.median_margin_paise) / 100
        verdict = "BEATS LUCK" if result.beats_luck else ""
        print(
            f"{result.strategy:<24}{summaries[result.strategy].family:<10}{result.trades:>8,}"
            f"{result.win_rate:>7.1%}{result.mean_r:>+9.3f}{result.t_stat:>+8.2f}{result.deflated:>10.3f}"
            f"{margin_rupees:>13,.0f}  {verdict}"
        )

    scored = [r for r in results if r.trades >= 30]
    if scored:
        print(f"\ntrials counted at scoring: {max(r.n_trials_at_scoring for r in scored):,}")
    print("'luck-adj' is the probability the result is real GIVEN how many spread configurations have been")
    print("tried. Only that column should reach a user-facing page. Above 0.95 is the bar.")
    print("mean R is in units of the MAXIMUM POSSIBLE LOSS on the spread, net of real costs on all four legs.")
    print("'margin/lot' is the real capital ONE lot of this spread needs — check it against actual capital")
    print("before treating any positive result as tradeable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
