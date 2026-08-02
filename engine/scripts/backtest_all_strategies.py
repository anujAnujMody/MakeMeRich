#!/usr/bin/env python
"""Backtests EVERY registered strategy on real option premiums and ranks
them by a luck-adjusted score.

Usage:
    python -m scripts.backtest_all_strategies
    python -m scripts.backtest_all_strategies --strategies orb60,supertrend
    python -m scripts.backtest_all_strategies --strikes-otm 0 --no-ledger

### What makes this different from the earlier sweeps

`scripts/sweep_strategies.py` measured throwaway research functions. This
runs the REAL strategies — the same objects the live engine calls, through
the same point-in-time gate — so a result here describes something that
could actually trade, and the run doubles as a stress test of the engine's
evaluation path (roughly 7 million evaluations across the full library).

### Read the deflated column, not the raw one

Thirty-two strategies is thirty-two chances for noise to look like skill.
The `deflated` column is the probability the result is real GIVEN how many
strategies have ever been tried, counted from the `TrialLedger` — which has
no delete path, so the count only ever grows. Every run appends to it, which
means re-running makes everything look slightly worse. That is the correct
incentive: more looking should buy less confidence, not more.

A strategy is only interesting at `deflated > 0.95`. Expect none to clear it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from decimal import Decimal
from functools import partial

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from te.backtest.results_store import save_results
from te.backtest.strategy_lab import BacktestResult, run_many
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
    parser.add_argument("--stop-pct", type=Decimal, default=Decimal(20))
    parser.add_argument("--target-pct", type=Decimal, default=Decimal(20))
    parser.add_argument("--max-hold-minutes", type=int, default=180)
    parser.add_argument("--strikes-otm", type=int, default=0, help="0 = at-the-money; higher = cheaper, further out")
    parser.add_argument(
        "--no-ledger",
        action="store_true",
        help=(
            "do not append to the trial ledger. For smoke tests ONLY — the deflated score is then computed "
            "against an undercounted N and is too generous to show anyone."
        ),
    )
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()

    chosen = [s.strip() for s in args.strategies.split(",") if s.strip()] or available()
    unknown = [s for s in chosen if s not in available()]
    if unknown:
        print(f"unknown strategies: {', '.join(unknown)}", file=sys.stderr)
        return 2

    settings = Settings()
    store = BarStore(settings.bar_store_path)
    cost_model = CostModel(load_charge_rate_table(settings.charges_path))
    lot_history = load_lot_size_history(settings.charges_path.parent / "lot_sizes.yaml")
    contracts = OptionContractIndex(store, args.instrument)
    ledger = None if args.no_ledger else TrialLedger(sa.create_engine(settings.database_url))
    run_id = args.run_id or f"all-strategies-{args.strikes_otm}otm"

    summaries = {s.name: s for s in all_specs()}
    print(f"Backtesting {len(chosen)} strategies on {args.instrument} option premiums.")
    print(f"stop -{args.stop_pct}% / target +{args.target_pct}% / max hold {args.max_hold_minutes}m / ", end="")
    print(f"strike {args.strikes_otm} out-of-the-money")
    if ledger is None:
        print("WARNING: --no-ledger — deflated scores below are TOO GENEROUS and must not be reported.")
    print()

    # ONE pass over history for the whole library — see `run_many`. Walking
    # the sessions once per strategy took ~8 minutes each, so over four
    # hours for 32.
    by_name = run_many(
        strategy_names=chosen,
        store=store,
        contracts=contracts,
        cost_model=cost_model,
        lot_size_for=partial(lot_size_on, lot_history, args.instrument),
        instrument=args.instrument,
        exchange=args.exchange,
        stop_pct=args.stop_pct,
        target_pct=args.target_pct,
        max_hold=dt.timedelta(minutes=args.max_hold_minutes),
        strikes_out_of_the_money=args.strikes_otm,
        trial_ledger=ledger,
        run_id=run_id,
    )
    results: list[BacktestResult] = [by_name[name] for name in chosen]
    for result in results:
        print(f"  {result.strategy:<24} trades={result.trades:<6,} unlabelled={result.unlabelled:,}")

    # Persisted so the Strategies page can show these. Skipped for
    # `--no-ledger` runs: those scores are computed against an undercounted
    # trial count and are too generous, so they must not become what the
    # dashboard displays.
    if ledger is not None:
        engine = sa.create_engine(settings.database_url)
        with sessionmaker(bind=engine)() as session:
            save_results(
                session,
                results,
                run_id=run_id,
                stop_pct=float(args.stop_pct),
                target_pct=float(args.target_pct),
                max_hold_minutes=args.max_hold_minutes,
                strikes_out_of_the_money=args.strikes_otm,
            )
            session.commit()
        print(f"  saved {len(results)} results for the dashboard\n")

    print(f"\n{'strategy':<24}{'family':<10}{'trades':>8}{'win %':>8}{'mean R':>9}{'t':>8}{'luck-adj':>10}  verdict")
    for result in sorted(results, key=lambda r: -r.deflated):
        if result.trades < 30:
            print(f"{result.strategy:<24}{summaries[result.strategy].family:<10}{result.trades:>8,}   too few trades")
            continue
        verdict = "BEATS LUCK" if result.beats_luck else ""
        print(
            f"{result.strategy:<24}{summaries[result.strategy].family:<10}{result.trades:>8,}"
            f"{result.win_rate:>7.1%}{result.mean_r:>+9.3f}{result.t_stat:>+8.2f}{result.deflated:>10.3f}  {verdict}"
        )

    scored = [r for r in results if r.trades >= 30]
    if scored:
        print(f"\ntrials counted at scoring: {max(r.n_trials_at_scoring for r in scored):,}")
    print("'luck-adj' is the probability the result is real GIVEN how many strategies have ever been")
    print("tried. It is the ONLY column that should reach a user-facing page. Above 0.95 is the bar.")
    print("mean R is in units of the stop, net of real brokerage/STT/GST.")
    print("Compare every row against `random_entry` — a strategy that cannot beat a coin flip is not a")
    print("strategy, whatever its win rate says.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
