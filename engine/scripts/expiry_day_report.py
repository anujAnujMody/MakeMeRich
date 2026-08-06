#!/usr/bin/env python
"""Does WHEN in the expiry cycle we buy change the answer?

    python -m scripts.expiry_day_report
    python -m scripts.expiry_day_report --capital 50000

### Why this is worth a run when nothing else was

Every buying result this project has produced pooled every days-to-expiry
together. `te.backtest.strategy_lab._label` simply asked the archive for the
nearest contract within 7 days and traded whatever came back — so a firing on
expiry morning and one six days out were averaged into a single number.

They are not the same instrument, and the difference is large in both
directions:

* **Cost.** From the real trade history: the same NIFTY lot cost Rs 2,746 on
  its expiry day and Rs 8,976 six days out. On a Rs 50,000 account that is
  the difference between 7 affordable lots and 1.
* **Behaviour.** A near-expiry option has almost no time value left to lose
  and moves nearly one-for-one with the index; a week out, most of the
  premium is time value that decays whether the direction is right or not.

And the account's only two winning trades were both expiry-day
(`NIFTY04AUG2624600PE`, +Rs 9,917 and +Rs 6,851), while both of the 6-days-out
trades lost. Two trades prove nothing — that is exactly why this measures it
across the whole archive instead.

The credit-spread lab APPEARED to control this from 2026-08-02
(`SpreadGeometry.min_days_to_expiry`) but did not: it applied the lower bound
AFTER asking for the nearest contract, and `nearest` stops at the first expiry
inside the upper bound — so a "3-7 days" band was handed tomorrow's contract,
rejected it, and recorded nothing, instead of the 6-day contract sitting
behind it in the chain. Every banded credit-spread result from that window is
therefore drawn from the minority of days that happened to have no nearer
expiry listed, and should not be trusted. Both labs were fixed on 2026-08-05
by moving the bound inside `nearest`.

### How to read it

`random_entry` runs alongside as the control, on the identical band. If a
band looks good for ORB and equally good for random, the band is what is
being measured — not the strategy. That is a real and useful finding, but it
is a finding about OPTION COST, not about an edge.

Reported in rupees per day, net of real dated brokerage/STT/GST, with the
live engine's daily-loss, consecutive-loss and drawdown limits enforced
during the replay.

A per-lot column runs alongside the per-day one, because the two answer
different questions. A near-expiry option is CHEAPER, so more lots fit inside
the same risk budget — which raises rupees per day without the trade itself
being any better. Rupees per lot strips that out. If a band wins on both, the
instrument is genuinely better; if it wins only per day, the band is simply
affordable and the strategy is unchanged.
"""

from __future__ import annotations

import argparse
import time
from decimal import Decimal
from functools import partial

import sqlalchemy as sa

from te.backtest.daily import build_daily_report, daily_net_from_strategy_trades
from te.backtest.strategy_lab import StrategyTrade, run_many
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.lot_size_history import load_lot_size_history, lot_size_on
from te.data.option_history import OptionContractIndex
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.ml.trials import TrialLedger
from te.settings import Settings

#: Bands, not single values. `0-0` is expiry day itself; the rest widen out
#: to the weekly cycle. Overlapping bands are deliberately NOT used — each
#: firing must land in exactly one, or the same trade would be counted in
#: several rows and the comparison would be between overlapping samples.
BANDS: tuple[tuple[int, int], ...] = ((0, 0), (1, 1), (2, 2), (3, 4), (5, 7))


def _rs(paise: int) -> str:
    return f"Rs {paise / 100:>10,.2f}"


def _daily(trades: list[StrategyTrade], *, capital: Paise) -> tuple[int, int, int, int, int, dict[int, int]]:
    """`(mean/day, per-lot mean, total, worst day, sessions, net by year)`.

    Rupees are derived by `daily_net_from_strategy_trades`, not recomputed
    here: it is the one place that knows a `StrategyTrade` contributes
    `net_paise_per_unit x lot_size x lots`, and duplicating that product is
    how the two would silently drift.
    """
    daily = daily_net_from_strategy_trades(trades)
    by_year: dict[int, int] = {}
    for day, net in daily.items():
        by_year[day.year] = by_year.get(day.year, 0) + net
    report = build_daily_report(daily, capital=capital, trade_count=len(trades))
    # Per LOT, so a band cannot win merely by being cheap enough to fit
    # more lots inside the same risk budget.
    lots = sum(t.lots for t in trades)
    per_lot = sum(t.net_paise_per_unit * t.lot_size for t in trades) // lots if lots else 0
    return (
        report.mean_daily_paise,
        per_lot,
        report.total_net_paise,
        report.worst_day_paise,
        report.sessions,
        by_year,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instrument", default="NIFTY")
    parser.add_argument("--exchange", default="NFO")
    parser.add_argument("--strategies", default="orb,random_entry")
    parser.add_argument("--capital", type=int, default=50_000)
    parser.add_argument("--risk-pct", type=Decimal, default=Decimal(3))
    parser.add_argument("--stop-pct", type=Decimal, default=Decimal(20))
    parser.add_argument("--target-pct", type=Decimal, default=Decimal(20))
    parser.add_argument("--max-daily-loss", type=int, default=2_000)
    parser.add_argument("--max-consecutive-losses", type=int, default=3)
    parser.add_argument("--max-drawdown-pct", type=Decimal, default=Decimal(20))
    parser.add_argument("--no-ledger", action="store_true")
    args = parser.parse_args()

    names = [s.strip() for s in args.strategies.split(",") if s.strip()]
    settings = Settings()
    store = BarStore(settings.bar_store_path)
    cost_model = CostModel(load_charge_rate_table(settings.charges_path))
    lot_history = load_lot_size_history(settings.charges_path.parent / "lot_sizes.yaml")
    contracts = OptionContractIndex(store, args.instrument)
    capital = Paise(args.capital * 100)
    ledger = None if args.no_ledger else TrialLedger(sa.create_engine(settings.database_url))

    print("=== DAYS TO EXPIRY -- BACKTEST, not a live or paper result ===")
    print(f"{args.instrument} {args.exchange} | strategies: {', '.join(names)}")
    print(f"capital Rs {args.capital:,} | risk {args.risk_pct}% | stop {args.stop_pct}% | ", end="")
    print(f"target {args.target_pct}% | daily loss cap Rs {args.max_daily_loss:,}")
    print("`random_entry` is the CONTROL — a band that helps it too is about cost, not edge.\n")

    started = time.monotonic()
    rows: list[tuple[str, tuple[int, int], int, int, int, int, int, int, int, dict[int, int]]] = []
    for lo, hi in BANDS:
        results, trades = run_many(
            strategy_names=names,
            store=store,
            contracts=contracts,
            cost_model=cost_model,
            lot_size_for=partial(lot_size_on, lot_history, args.instrument),
            instrument=args.instrument,
            exchange=args.exchange,
            stop_pct=args.stop_pct,
            target_pct=args.target_pct,
            min_days_to_expiry=lo,
            max_days_to_expiry=hi,
            capital=capital,
            risk_budget_pct=args.risk_pct,
            max_position_size_pct=Decimal(50),
            max_daily_loss_paise=args.max_daily_loss * 100,
            max_consecutive_losses=args.max_consecutive_losses,
            max_drawdown_pct=args.max_drawdown_pct,
            compound_equity=True,
            collect_trades=True,
            trial_ledger=ledger,
            run_id=f"expiry-band-{lo}-{hi}",
        )
        for name in names:
            mean, per_lot, total, worst, sessions, by_year = _daily(trades[name], capital=capital)
            rows.append(
                (
                    name, (lo, hi), mean, per_lot, total, worst, sessions,
                    results[name].unaffordable, results[name].unlabelled, by_year,
                )
            )
        print(
            f"  ... dte {lo}-{hi}: "
            + ", ".join(f"{n}={len(trades[n])} trades" for n in names),
            flush=True,
        )

    print(f"\nran {len(BANDS)} bands x {len(names)} strategies in {(time.monotonic() - started) / 60:.1f} min\n")

    # `unlabelled` sits beside `unaffordable` because they mean opposite
    # things and only one of them was printed before. `unaffordable` is a
    # signal the account could not fund; `unlabelled` is a signal the
    # ARCHIVE could not resolve a contract — and since the whole point of
    # this script is to change which contracts resolve, a band with no
    # coverage would otherwise be indistinguishable from a band that was
    # fully covered and simply lost.
    print(
        f"{'strategy':<14}{'dte':>7}{'days':>7}{'Rs/day':>13}{'Rs/lot':>12}"
        f"{'total':>14}{'worst day':>13}{'unafford':>10}{'nodata':>8}"
    )
    for name, (lo, hi), mean, per_lot, total, worst, sessions, unaffordable, unlabelled, _ in rows:
        blank = "  (no trades)" if sessions == 0 else ""
        print(
            f"{name:<14}{f'{lo}-{hi}':>7}{sessions:>7}{_rs(mean):>13}{_rs(per_lot):>12}"
            f"{_rs(total):>14}{_rs(worst):>13}{unaffordable:>10}{unlabelled:>8}{blank}"
        )

    print("\n=== per year, so one good year cannot hide inside an average ===")
    for name, (lo, hi), *_rest, by_year in rows:
        rendered = "  ".join(f"{y}:{v // 100:+,}" for y, v in sorted(by_year.items())) or "(no trades)"
        print(f"  {name:<14} dte {lo}-{hi}   {rendered}")

    print("\n=== the comparison that decides it ===")
    for lo, hi in BANDS:
        picked = {name: next(r for r in rows if r[0] == name and r[1] == (lo, hi)) for name in names}
        if any(picked[n][6] == 0 for n in picked):
            # A band nobody traded is NOT a measured zero. Printing
            # "Rs 0.00 vs Rs 0.00" here made an empty band typographically
            # identical to one measured over 600 sessions.
            print(f"  dte {lo}-{hi}:  no data — at least one strategy took no trades in this band")
            continue
        if "orb" in picked and "random_entry" in picked:
            edge_day = picked["orb"][2] - picked["random_entry"][2]
            edge_lot = picked["orb"][3] - picked["random_entry"][3]
            print(
                f"  dte {lo}-{hi}:  per day  orb {_rs(picked['orb'][2])} vs random "
                f"{_rs(picked['random_entry'][2])} -> {_rs(edge_day)}"
            )
            print(
                f"{'':13}per lot  orb {_rs(picked['orb'][3])} vs random "
                f"{_rs(picked['random_entry'][3])} -> {_rs(edge_lot)}"
            )
    print("\nA band where ORB beats random by a real margin, on a real number of trades,")
    print("is the only thing here worth acting on. A band where both improve together is")
    print("telling you the OPTION was cheaper, not that the STRATEGY was better.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
