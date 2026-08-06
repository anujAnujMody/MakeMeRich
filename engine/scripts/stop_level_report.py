#!/usr/bin/env python
"""How big should the rupee stop actually be? Measured, in rupees per day.

    docker exec trading-engine-1 python3 -m scripts.stop_level_report

### Why this exists

`Settings.paper_cycle_max_loss_per_trade_paise` shipped at Rs 700 on
2026-08-06. That number came from the owner, after a NIFTY trade lost
Rs 2,272 on a Rs 50,000 account — a reasonable reaction to a real event, and
still an unmeasured choice. Nothing had compared it with Rs 500 or Rs 1,500.

An earlier 144-combination sweep did vary the stop, but as a PERCENTAGE of
premium, and its results were printed to stdout and never persisted. They are
gone. This measures the thing actually in force: a rupee cap, one lot, and
the exits the live engine really runs.

### What is held fixed, and why that matters

Everything except the stop, at the values live on 2026-08-06: 1 lot, a target
at 10x the risk, and the STEPPED profit lock (+15% rungs, 5% buffer) that
replaced the one-time lock the same day. Changing the stop changes the risk
unit, so the target moves with it by construction — the reward:risk ratio is
held, not the rupee target.

### The daily limits are applied, deliberately

Trades are walked in time order and the live day-limits enforced as they
land: `max_trades_per_day` and the daily-loss halt. Without this the report
answers "what would these firings have earned" rather than "what would this
engine have earned", and those differ by exactly the days the engine would
have stopped. Both are printed, so the cost of the limits is visible rather
than assumed.

### Reading the output

Rupees per day, net of real brokerage/STT/GST at the lot size in force on
each day. Look for a PLATEAU, not a peak — per `backtest-expert`, if Rs 500,
Rs 700 and Rs 1,000 all behave similarly that is a real effect, and if only
Rs 700 works it is noise wearing a costume. Six levels are tested, so one
will look best by luck.

`t vs Rs 700` is a PAIRED comparison against the level currently live: the
same firings, the same bars, so the per-trade differences are matched.
Unpaired means of a distribution this heavy-tailed would call almost anything
insignificant.

None of these are expected to be profitable. ORB scores the same as random
entry; this asks which stop loses least, not which one wins.
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from te.backtest.daily import build_daily_report
from te.backtest.strategy_lab import StrategyTrade, run_many
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.lot_size_history import load_lot_size_history, lot_size_on
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.costs import CostModel
from te.domain.geometry import RupeeRiskGeometry
from te.domain.money import Paise
from te.domain.signal import ExitPlan
from te.engine.exits import OpenPosition, evaluate_position
from te.settings import Settings

#: The live geometry, minus the one thing being swept.
TARGET_MULTIPLE = Decimal(10)
LOCK_ACTIVATION_PCT = Decimal(15)
LOCK_BUFFER_PCT = Decimal(5)
MAX_HOLD = dt.timedelta(hours=3)
HARD_EXIT_BY = dt.time(15, 15)

#: The live account, so the drawdown percentage means something.
CAPITAL = Paise(50_000_00)
MAX_TRADES_PER_DAY = 3
MAX_DAILY_LOSS_PAISE = 250_000

#: Rs 700 is the live value and sits in the middle on purpose — a sweep whose
#: incumbent is at the edge of the grid cannot show a plateau around it.
STOP_LEVELS_PAISE = (30_000, 50_000, 70_000, 100_000, 150_000, 200_000)


@dataclass(frozen=True)
class Walked:
    day: dt.date
    entry_ts: dt.datetime
    net_paise: int
    reason: str


def _walk(
    *,
    store: BarStore,
    trade: StrategyTrade,
    lot_size: int,
    cost_model: CostModel,
    max_loss_paise: int,
) -> Walked | None:
    entry_premium = Paise(trade.entry_premium_paise)
    if int(entry_premium) <= 0:
        return None
    stop_distance = max_loss_paise // lot_size
    if stop_distance <= 0 or stop_distance >= int(entry_premium):
        return None

    # Built through the REAL geometry, not by hand. The first version of this
    # script assembled `ExitPlan` itself and crashed on its validator at a
    # Rs 300 stop, because a small rupee risk pulls the 10x target in nearer
    # than the +15% lock trigger. Going through `RupeeRiskGeometry` means the
    # sweep measures exactly what the live engine would do — including that
    # rule's own decision to report an unreachable lock as off.
    levels = RupeeRiskGeometry(
        max_loss_paise=max_loss_paise,
        target_multiple=TARGET_MULTIPLE,
        profit_lock_activation_pct=LOCK_ACTIVATION_PCT,
        profit_lock_buffer_pct=LOCK_BUFFER_PCT,
    ).levels(entry_premium, quantity=lot_size)
    plan = ExitPlan(
        entry_premium=entry_premium,
        stop=levels.stop,
        trailing_distance=levels.trailing_distance,
        target=levels.target,
        max_hold=MAX_HOLD,
        hard_exit_by=HARD_EXIT_BY,
        profit_lock_activation=levels.profit_lock_activation,
        profit_lock_buffer_pct=levels.profit_lock_buffer_pct,
    )
    position = OpenPosition(
        symbol=trade.option_symbol,
        exchange="NFO",
        strategy="orb",
        direction=trade.direction,  # type: ignore[arg-type]
        lot_size=lot_size,
        lots=1,
        opened_at=trade.entry_ts,
        exit_plan=plan,
        current_stop=plan.stop,
    )

    day = trade.entry_ts.astimezone(IST).date()
    day_end = dt.datetime.combine(day, dt.time(15, 30), tzinfo=IST)
    bars = store.read(symbol=trade.option_symbol, start=trade.entry_ts, end=day_end, interval="1m")
    if bars.empty:
        return None

    exit_premium: Paise | None = None
    reason = "time"
    for row in bars.sort_values("event_ts").itertuples():
        premium = Paise(round(float(row.c) * 100))
        if int(premium) <= 0:
            continue
        position, decision = evaluate_position(
            position, current_premium=premium, now=row.event_ts.to_pydatetime().astimezone(IST)
        )
        if decision is not None:
            exit_premium, reason = decision.exit_premium, decision.reason
            break
    if exit_premium is None:
        exit_premium = Paise(round(float(bars.sort_values("event_ts").iloc[-1]["c"]) * 100))

    gross = (int(exit_premium) - int(entry_premium)) * lot_size
    charges = int(
        cost_model.round_trip(
            entry_premium=entry_premium,
            exit_premium=exit_premium,
            qty=lot_size,
            exchange="NFO",
            on=day,
        ).total
    )
    return Walked(day=day, entry_ts=trade.entry_ts, net_paise=gross - charges, reason=reason)


def _apply_daily_limits(walked: list[Walked]) -> list[Walked]:
    """The live day-limits, applied in time order.

    `max_trades_per_day` and the daily-loss halt both stop NEW entries only;
    a position already open keeps its exits. Since this walks one position at
    a time to completion, that distinction collapses to "drop every firing
    after the limit binds", which is what happens here.
    """
    kept: list[Walked] = []
    by_day: dict[dt.date, list[Walked]] = defaultdict(list)
    for w in walked:
        by_day[w.day].append(w)
    for day in sorted(by_day):
        taken = 0
        running = 0
        for w in sorted(by_day[day], key=lambda x: x.entry_ts):
            if taken >= MAX_TRADES_PER_DAY:
                break
            if running <= -MAX_DAILY_LOSS_PAISE:
                break
            kept.append(w)
            taken += 1
            running += w.net_paise
    return kept


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument", default="NIFTY")
    parser.add_argument("--strategy", default="orb")
    args = parser.parse_args()

    settings = Settings()
    store = BarStore(settings.bar_store_path)
    contracts = OptionContractIndex(store, args.instrument)
    cost_model = CostModel(load_charge_rate_table(settings.charges_path))
    lot_history = load_lot_size_history(settings.charges_path.parent / "lot_sizes.yaml")

    def lot_size_for(day: dt.date) -> int:
        return lot_size_on(lot_history, args.instrument, day)

    print(f"labelling {args.strategy} firings on {args.instrument} ...", flush=True)
    _results, trades_by_strategy = run_many(
        strategy_names=[args.strategy],
        store=store,
        contracts=contracts,
        cost_model=cost_model,
        lot_size_for=lot_size_for,
        instrument=args.instrument,
        collect_trades=True,
        run_id="stop-level-sweep",
    )
    firings = sorted(trades_by_strategy[args.strategy], key=lambda t: t.entry_ts)
    print(f"{len(firings)} firings\n", flush=True)

    per_level: dict[int, list[Walked]] = {}
    for level in STOP_LEVELS_PAISE:
        walked = [
            w
            for trade in firings
            if (
                w := _walk(
                    store=store,
                    trade=trade,
                    lot_size=lot_size_for(trade.entry_ts.astimezone(IST).date()),
                    cost_model=cost_model,
                    max_loss_paise=level,
                )
            )
            is not None
        ]
        per_level[level] = walked
        print(f"  Rs {level // 100:5}: {len(walked)} walked", flush=True)

    for label, limited in (("NO day limits", False), ("WITH live day limits (3 trades, Rs 2,500)", True)):
        print(f"\n=== {label} ===")
        header = (
            f"{'stop':>7} {'trades':>7} {'total Rs':>11} {'Rs/day':>8} {'median':>8} "
            f"{'worst day':>10} {'>=500':>6} {'maxDD%':>7} {'win%':>6}"
        )
        print(header)
        print("-" * len(header))
        for level, walked in per_level.items():
            rows = _apply_daily_limits(walked) if limited else walked
            if not rows:
                print(f"{level // 100:7} {'no data':>7}")
                continue
            daily: dict[dt.date, int] = defaultdict(int)
            for w in rows:
                daily[w.day] += w.net_paise
            report = build_daily_report(daily, capital=CAPITAL, trade_count=len(rows))
            wins = sum(1 for w in rows if w.net_paise > 0)
            print(
                f"{level // 100:7} {report.trades:7} {report.total_net_paise / 100:11,.0f} "
                f"{report.mean_daily_paise / 100:8,.1f} {report.median_daily_paise / 100:8,.1f} "
                f"{report.worst_day_paise / 100:10,.0f} {report.days_at_or_above_500:6} "
                f"{report.max_drawdown_pct_of_capital:7.1f} {wins / len(rows) * 100:6.1f}"
            )

    # Paired on the INTERSECTION, keyed by the firing itself. The first
    # version zipped the two lists and required equal lengths, which silently
    # printed nothing at all: a wide stop is refused on a cheap option
    # (`stop_distance >= premium`), so the levels walk different subsets —
    # 1,280 firings at Rs 300 against 1,140 at Rs 2,000. A comparison that
    # quietly produces no rows is worse than one that errors.
    print("\npaired vs the live Rs 700 (same firings only, so counts differ per row)")
    print(f"{'stop':>7} {'paired n':>9} {'mean diff Rs':>13} {'t':>7}")
    base = {(w.entry_ts, w.day): w for w in per_level[70_000]}
    for level, walked in per_level.items():
        if level == 70_000:
            continue
        diffs = [
            (w.net_paise - base[(w.entry_ts, w.day)].net_paise) / 100
            for w in walked
            if (w.entry_ts, w.day) in base
        ]
        if len(diffs) < 2:
            print(f"{level // 100:7} {len(diffs):9} {'too few paired firings':>13}")
            continue
        mean = statistics.fmean(diffs)
        sd = statistics.stdev(diffs)
        t = mean / (sd / len(diffs) ** 0.5) if sd > 0 else float("nan")
        print(f"{level // 100:7} {len(diffs):9} {mean:13,.1f} {t:7.2f}")
    print("\n|t| > 2 is the usual bar. Six levels were tried, so treat a marginal t as weaker.")

    # A drawdown above 100% of capital is not a drawdown, it is RUIN — the
    # account was gone and every trade after that point is imaginary. The
    # table above reports drawdown as a percentage without saying so, and
    # several rows exceed 100%, so the honest reading has to be spelled out
    # rather than left for the reader to notice.
    print("\nruin check — the day the account would actually have run out")
    print(f"{'stop':>7} {'survived?':>10} {'ruined on':>12} {'trades before':>14}")
    for level, walked in per_level.items():
        rows = _apply_daily_limits(walked)
        equity = int(CAPITAL)
        ruined_on: dt.date | None = None
        taken = 0
        for w in sorted(rows, key=lambda x: x.entry_ts):
            equity += w.net_paise
            taken += 1
            if equity <= 0:
                ruined_on = w.day
                break
        if ruined_on is None:
            print(f"{level // 100:7} {'yes':>10} {'-':>12} {taken:14}")
        else:
            print(f"{level // 100:7} {'NO':>10} {ruined_on.isoformat():>12} {taken:14}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
