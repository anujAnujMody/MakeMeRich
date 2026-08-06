#!/usr/bin/env python
"""Reports one strategy's real historical performance in RUPEES PER DAY, not
R-multiples: mean/median/best/worst day, days that cleared Rs 500/Rs 1000,
losing streaks, and drawdown against a stated starting capital.

Usage:
    python -m scripts.daily_income_report
    python -m scripts.daily_income_report --strategy orb --capital 50000

### This is a BACKTEST

Every number below comes from `te.backtest.strategy_lab.run_many`, which
prices each firing off the REAL option contract's own recorded premium bars
(`OptionContractIndex.nearest` + `label_one_firing_on_premium`) — NOT
`te.backtest.engine.run_backtest`, which prices trades off the underlying
INDEX's own bars as a premium proxy (see that module's own comment on why
that stand-in exists: there is no separate option-premium bar series on that
path). Pricing off the index instead of a real premium overstates a NIFTY
trade by roughly two orders of magnitude (~Rs 24,600 vs a real ~Rs 50-300
premium), which is why this report does not use it. Nothing here has ever
traded a rupee — see `honest-metrics` on why that distinction must stay
explicit at every display site, including this one.

### Unaffordable signals are counted, never hidden

Every firing that reached a real, labelled outcome is then sized via
`te.risk.sizing.size_position` against `--capital`/`--risk-pct`. A signal
`size_position` rejects (too little capital for even one lot, or not enough
edge over real costs) is real information about the strategy at this
capital — on small capital it can be MOST of the signals. Hiding that count
would make the strategy look more tradeable than it is, so it is printed
alongside the trade count, never folded into either.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from decimal import Decimal
from functools import partial

from te.backtest.daily import build_daily_report, daily_net_from_strategy_trades
from te.backtest.strategy_lab import run_many
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.lot_size_history import load_lot_size_history, lot_size_on
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.costs import CostModel
from te.domain.money import Paise, rupees
from te.settings import Settings


def _instrument_session_dates(store: BarStore, instrument: str) -> list[dt.date]:
    """Every distinct IST session date recorded for `instrument`, sorted."""
    frame = store.read(
        symbol=instrument,
        start=dt.datetime(2000, 1, 1, tzinfo=dt.UTC),
        end=dt.datetime(2100, 1, 1, tzinfo=dt.UTC),
        interval="1m",
    )
    if frame.empty:
        return []
    dates = frame["event_ts"].dt.tz_convert(IST).dt.date.unique().tolist()
    return sorted(dates)


def _rupees(paise_amount: int) -> str:
    return f"Rs {rupees(Paise(paise_amount)):,.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instrument", default="NIFTY")
    parser.add_argument("--exchange", default="NFO")
    parser.add_argument("--strategy", default="orb")
    parser.add_argument("--capital", type=int, default=30_000, help="starting capital, in RUPEES")
    parser.add_argument(
        "--risk-pct", type=Decimal, default=Decimal("1.5"), help="risk budget, as a percent of capital per trade"
    )
    parser.add_argument("--stop-pct", type=Decimal, default=Decimal(20))
    parser.add_argument("--target-pct", type=Decimal, default=Decimal(20))
    parser.add_argument("--strikes-otm", type=int, default=0, help="0 = at-the-money; higher = cheaper, further out")
    parser.add_argument(
        "--max-daily-loss",
        type=int,
        default=1_000,
        help="in RUPEES: no further entries THAT DAY once the day's realized net breaches this (0 disables)",
    )
    parser.add_argument(
        "--max-consecutive-losses",
        type=int,
        default=3,
        help="stand down for the rest of the day after this many straight losers (0 disables)",
    )
    parser.add_argument(
        "--max-drawdown-pct",
        type=Decimal,
        default=Decimal(20),
        help="stop the run once equity drawdown from its peak reaches this percent (100 disables)",
    )
    parser.add_argument(
        "--no-compound",
        action="store_true",
        help="size every trade off the fixed starting capital instead of running equity "
        "(matches every trade betting the SAME stake regardless of what has already been lost)",
    )
    args = parser.parse_args()

    # Mirrors the live engine's own halts (`te.risk.limits.check_daily_loss_limit`/
    # `check_consecutive_losses`/`check_max_drawdown`) via the SAME pure predicates
    # those functions call -- see `te.backtest.strategy_lab.run_many`'s docstring.
    # `None` is this script's own "disabled" sentinel for a CLI run that wants the
    # pre-enforcement (dishonest) numbers for comparison.
    max_daily_loss_paise = Paise(args.max_daily_loss * 100) if args.max_daily_loss > 0 else None
    max_consecutive_losses = args.max_consecutive_losses if args.max_consecutive_losses > 0 else None
    max_drawdown_pct = args.max_drawdown_pct if args.max_drawdown_pct < 100 else None
    compound_equity = not args.no_compound

    settings = Settings()
    store = BarStore(settings.bar_store_path)
    cost_model = CostModel(load_charge_rate_table(settings.charges_path))
    lot_history = load_lot_size_history(settings.charges_path.parent / "lot_sizes.yaml")
    contracts = OptionContractIndex(store, args.instrument)

    session_dates = _instrument_session_dates(store, args.instrument)
    if not session_dates:
        print(f"no recorded bars for {args.instrument} in {settings.bar_store_path}", file=sys.stderr)
        return 2

    capital_paise = Paise(args.capital * 100)

    results, trades_by_name = run_many(
        strategy_names=[args.strategy],
        store=store,
        contracts=contracts,
        cost_model=cost_model,
        lot_size_for=partial(lot_size_on, lot_history, args.instrument),
        instrument=args.instrument,
        exchange=args.exchange,
        stop_pct=args.stop_pct,
        target_pct=args.target_pct,
        strikes_out_of_the_money=args.strikes_otm,
        capital=capital_paise,
        risk_budget_pct=args.risk_pct,
        # Both of these were left at `run_many`'s UNLIMITED defaults (no
        # minimum edge over cost, no position cap), while the live paper
        # cycle enforces `paper_cycle_min_edge_multiple` and a real cap. This
        # report therefore took trades the live engine would have refused for
        # insufficient edge, inflating both the trade count and the P&L — and
        # it disagreed with `scripts/sweep_parameters.py`, which does pass
        # them, so the two reports were not comparable. Caught in review
        # 2026-08-05.
        min_edge_multiple=settings.paper_cycle_min_edge_multiple,
        max_position_size_pct=Decimal(25),
        collect_trades=True,
        max_daily_loss_paise=max_daily_loss_paise,
        max_consecutive_losses=max_consecutive_losses,
        max_drawdown_pct=max_drawdown_pct,
        compound_equity=compound_equity,
    )
    result = results[args.strategy]
    trades = trades_by_name[args.strategy]

    daily_net = daily_net_from_strategy_trades(trades)
    report = build_daily_report(daily_net, capital=capital_paise, trade_count=len(trades))

    start_date, end_date = session_dates[0], session_dates[-1]

    print("=== BACKTEST -- not a live or paper result ===")
    print(f"strategy={args.strategy} instrument={args.instrument} exchange={args.exchange}")
    print(f"date range: {start_date} .. {end_date}")
    print(f"sessions in recorded history: {len(session_dates)}")
    print(f"capital: {_rupees(int(capital_paise))}   risk per trade: {args.risk_pct}% of capital")
    print(f"stop -{args.stop_pct}% / target +{args.target_pct}% / strike {args.strikes_otm} out-of-the-money")
    print(
        "risk enforcement: "
        f"max daily loss={'Rs ' + str(args.max_daily_loss) if max_daily_loss_paise is not None else 'disabled'}  "
        f"max consecutive losses={max_consecutive_losses if max_consecutive_losses is not None else 'disabled'}  "
        f"max drawdown={str(max_drawdown_pct) + '%' if max_drawdown_pct is not None else 'disabled'}  "
        f"compounding={'ON' if compound_equity else 'OFF (fixed capital every trade)'}"
    )
    print(f"closed trades: {result.trades}")
    print(f"unlabelled signals (no usable premium data): {result.unlabelled}")
    print(f"unaffordable signals (rejected by size_position at this capital): {result.unaffordable}")
    print(f"days cut short by the daily loss limit: {result.halted_days}")
    print(f"days cut short by consecutive losses: {result.standdown_days}")
    print(f"run stopped early by max drawdown / account wipe-out: {result.drawdown_halted}")
    print(f"final equity: {_rupees(result.final_equity_paise)}")
    print()

    if report.sessions == 0:
        print("no closed trades over this window -- nothing to report.")
        return 0

    print(f"{'sessions with a closed trade':<32}{report.sessions:>16,}")
    print(f"{'total net':<32}{_rupees(report.total_net_paise):>16}")
    print(f"{'mean per session':<32}{_rupees(report.mean_daily_paise):>16}")
    print(f"{'median per session':<32}{_rupees(report.median_daily_paise):>16}")
    print(f"{'best session':<32}{_rupees(report.best_day_paise):>16}")
    print(f"{'worst session':<32}{_rupees(report.worst_day_paise):>16}")
    print(f"{'sessions >= Rs 500':<32}{report.days_at_or_above_500:>16,}")
    print(f"{'sessions >= Rs 1000':<32}{report.days_at_or_above_1000:>16,}")
    print(f"{'losing sessions':<32}{report.losing_days:>16,}")
    print(f"{'longest losing streak':<32}{report.longest_losing_streak:>16,}")
    print(f"{'max drawdown':<32}{_rupees(report.max_drawdown_paise):>16}")
    print(f"{'max drawdown % of capital':<32}{report.max_drawdown_pct_of_capital:>15.2f}%")
    print(f"{'longest days underwater':<32}{report.longest_days_underwater:>16,}")
    print()
    print("monthly net:")
    for month, net in sorted(report.monthly_net_paise.items()):
        print(f"  {month}  {_rupees(net):>16}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
