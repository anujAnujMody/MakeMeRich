#!/usr/bin/env python
"""Does the STEPPED profit-lock ratchet beat the one-time lock, or nothing?

    docker exec trading-engine-1 python3 -m scripts.profit_lock_report

### What is being compared

Three exit configurations, over the same real ORB firings and the same
recorded option-premium bars:

    none      stop and target only
    one_time  the lock fires once at +15% and freezes  (behaviour until 2026-08-06)
    ratchet   the lock steps up again at every further +15%  (asked for by the owner)

### Why this walks the LIVE exit engine

Every other backtest in this repo labels a firing against fixed barriers.
That cannot answer this question: the lock is a STATEFUL rule whose stop
depends on the path taken, so it has to be evaluated bar by bar. This script
therefore drives `te.engine.exits.evaluate_position` — the exact function the
live cycle calls — over each firing's own minute bars.

The cost of that honesty is that this is slower than a barrier sweep, and
that it is only as good as the recorded premium history.

### Reading the output

Reported in RUPEES, net of real brokerage/STT/GST on both legs, at the lot
size in force on the trade's own day. Read the whole table, not the winning
row: three configurations is three chances for noise to look like skill, and
the differences here are expected to be small. A higher win rate with a flat
or slightly lower total is the SHAPE this rule is known to produce — that was
already true of the one-time lock (50.7% vs 46.2% win rate at roughly
unchanged mean R over 1,305 trades) and is not, on its own, an improvement in
expectancy.

What would justify the ratchet is a materially fatter right tail — the whole
point is that a big winner is no longer capped at the first rung — with total
net not worse. If the ratchet only trades winners for a higher win rate, it
is a lateral move and should be said so.
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
import sys
from dataclasses import dataclass, replace
from decimal import Decimal

from te.backtest.strategy_lab import StrategyTrade, run_many
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.lot_size_history import load_lot_size_history, lot_size_on
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.domain.signal import ExitPlan
from te.engine.exits import OpenPosition, evaluate_position
from te.settings import Settings

#: The live geometry as of 2026-08-06 — see `Settings.paper_cycle_*`.
MAX_LOSS_PAISE = 70_000
TARGET_MULTIPLE = Decimal(10)
LOCK_ACTIVATION_PCT = Decimal(15)
LOCK_BUFFER_PCT = Decimal(5)
MAX_HOLD = dt.timedelta(hours=3)
HARD_EXIT_BY = dt.time(15, 15)


@dataclass(frozen=True)
class Outcome:
    net_rupees: float
    reason: str


def _walk(
    *,
    store: BarStore,
    trade: StrategyTrade,
    lot_size: int,
    cost_model: CostModel,
    lock: str,
) -> Outcome | None:
    """One firing, walked minute by minute through the REAL exit engine."""
    entry_premium = Paise(trade.entry_premium_paise)
    if int(entry_premium) <= 0:
        return None
    stop_distance = MAX_LOSS_PAISE // lot_size
    if stop_distance <= 0 or stop_distance >= int(entry_premium):
        return None

    activation = buffer_pct = None
    if lock in ("one_time", "ratchet"):
        activation = Paise(int(entry_premium) + int(entry_premium) * int(LOCK_ACTIVATION_PCT) // 100)
        buffer_pct = LOCK_BUFFER_PCT

    plan = ExitPlan(
        entry_premium=entry_premium,
        stop=Paise(int(entry_premium) - stop_distance),
        trailing_distance=None,
        target=Paise(int(entry_premium) + int(Decimal(stop_distance) * TARGET_MULTIPLE)),
        max_hold=MAX_HOLD,
        hard_exit_by=HARD_EXIT_BY,
        profit_lock_activation=activation,
        profit_lock_buffer_pct=buffer_pct,
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

    day_end = dt.datetime.combine(trade.entry_ts.astimezone(IST).date(), dt.time(15, 30), tzinfo=IST)
    bars = store.read(symbol=trade.option_symbol, start=trade.entry_ts, end=day_end, interval="1m")
    if bars.empty:
        return None

    exit_premium: Paise | None = None
    reason = "time"
    for row in bars.sort_values("event_ts").itertuples():
        premium = Paise(round(float(row.c) * 100))
        if int(premium) <= 0:
            continue
        now = row.event_ts.to_pydatetime().astimezone(IST)
        position, decision = evaluate_position(position, current_premium=premium, now=now)
        if lock == "one_time" and position.profit_lock_engaged:
            # Re-assert the ORIGINAL one-time semantics by stripping the lock
            # from the plan once it has fired: with no activation configured
            # the rule cannot step up again, and the stop it already set
            # stays exactly where it is.
            #
            # Done AFTER the call, not before, because rung one is identical
            # under both variants — so this cannot change the first fire, only
            # suppress the ones the old behaviour never had.
            position = replace(
                position,
                exit_plan=replace(
                    position.exit_plan, profit_lock_activation=None, profit_lock_buffer_pct=None
                ),
            )
        if decision is not None:
            exit_premium, reason = decision.exit_premium, decision.reason
            break
    if exit_premium is None:
        last = bars.sort_values("event_ts").iloc[-1]
        exit_premium = Paise(round(float(last["c"]) * 100))

    gross = (int(exit_premium) - int(entry_premium)) * lot_size
    charges = int(
        cost_model.round_trip(
            entry_premium=entry_premium,
            exit_premium=exit_premium,
            qty=lot_size,
            exchange="NFO",
            on=trade.entry_ts.astimezone(IST).date(),
        ).total
    )
    return Outcome(net_rupees=(gross - charges) / 100, reason=reason)


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
        run_id="profit-lock-ratchet-eval",
    )
    firings = trades_by_strategy[args.strategy]
    print(f"{len(firings)} firings\n", flush=True)

    rows: dict[str, list[Outcome]] = {}
    for lock in ("none", "one_time", "ratchet"):
        outcomes: list[Outcome] = []
        for trade in firings:
            lot_size = lot_size_for(trade.entry_ts.astimezone(IST).date())
            outcome = _walk(
                store=store, trade=trade, lot_size=lot_size, cost_model=cost_model, lock=lock
            )
            if outcome is not None:
                outcomes.append(outcome)
        rows[lock] = outcomes
        print(f"  {lock}: {len(outcomes)} walked", flush=True)

    print()
    header = f"{'config':10} {'n':>5} {'total Rs':>12} {'mean Rs':>9} {'win%':>6} {'best Rs':>10} {'p90 Rs':>9}"
    print(header)
    print("-" * len(header))
    for lock, outcomes in rows.items():
        if not outcomes:
            print(f"{lock:10} {'no data':>5}")
            continue
        nets = [o.net_rupees for o in outcomes]
        wins = sum(1 for n in nets if n > 0)
        p90 = statistics.quantiles(nets, n=10)[-1] if len(nets) >= 10 else max(nets)
        print(
            f"{lock:10} {len(nets):5} {sum(nets):12,.0f} {statistics.fmean(nets):9,.1f} "
            f"{wins / len(nets) * 100:6.1f} {max(nets):10,.0f} {p90:9,.0f}"
        )

    print("\nexit reasons")
    for lock, outcomes in rows.items():
        counts: dict[str, int] = {}
        for o in outcomes:
            counts[o.reason] = counts.get(o.reason, 0) + 1
        print(f"  {lock:10}", dict(sorted(counts.items(), key=lambda kv: -kv[1])))

    # PAIRED comparison, which is the only honest one here: every config sees
    # the SAME firings on the SAME bars, so the per-trade differences are
    # matched and their variance is far smaller than the variance of the raw
    # per-trade P&L. Comparing two unpaired means of a distribution this
    # heavy-tailed would call almost anything insignificant.
    print("\npaired differences (same firing, same bars)")
    print(f"{'comparison':22} {'mean diff Rs':>13} {'t':>7} {'better on':>10} {'worse on':>9}")
    pairs = (("ratchet", "one_time"), ("ratchet", "none"), ("one_time", "none"))
    for a, b in pairs:
        if not rows[a] or not rows[b]:
            continue
        diffs = [x.net_rupees - y.net_rupees for x, y in zip(rows[a], rows[b], strict=True)]
        mean = statistics.fmean(diffs)
        sd = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
        t = mean / (sd / len(diffs) ** 0.5) if sd > 0 else float("nan")
        better = sum(1 for d in diffs if d > 0)
        worse = sum(1 for d in diffs if d < 0)
        print(f"{a + ' vs ' + b:22} {mean:13,.1f} {t:7.2f} {better:10} {worse:9}")
    print(
        "\n|t| > 2 is the usual 'unlikely to be chance' bar. Three configurations were\n"
        "compared, so treat a marginal t as weaker than it looks."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
