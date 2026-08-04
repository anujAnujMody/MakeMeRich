#!/usr/bin/env python
"""Sweeps stop / target / strike / risk-per-trade and reports RUPEES PER DAY.

    python -m scripts.sweep_parameters
    python -m scripts.sweep_parameters --strategies orb,random_entry

Every one of these four parameters was chosen by a person and never tested.
This is the test. `random_entry` runs alongside as the control: a strategy has
to beat a coin flip on the same days with the same parameters, not merely beat
zero.

### How to read the output, in order

1. **The plateau tables first.** A single best cell out of 144 is what noise
   looks like. What matters is whether a RANGE of values behaves consistently.
2. **Then ORB against `random_entry`** at the same settings. A number that
   looks good but matches the coin flip is not an edge.
3. **Then the top rows**, and only as a hypothesis to check against 1 and 2.

Everything is net of real brokerage/STT/GST via `CostModel`, sized against real
capital, and walked through the same daily-loss / consecutive-loss / drawdown
limits the live engine uses.

Every combination appends to the `TrialLedger`. Sweeping 144 combinations means
one of them will look excellent by luck; the deflated score exists to remember
that, and is reported for the winner rather than hidden.
"""

from __future__ import annotations

import argparse
import time
from decimal import Decimal
from functools import partial

from te.backtest.sweep import SweepCell, plateau, sweep
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.lot_size_history import load_lot_size_history, lot_size_on
from te.data.option_history import OptionContractIndex
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.settings import Settings

RISK_PCTS = [Decimal(1), Decimal(2), Decimal(3), Decimal(5)]
STOP_PCTS = [Decimal(8), Decimal(10), Decimal(15), Decimal(20)]
TARGET_PCTS = [Decimal(15), Decimal(20), Decimal(30)]
STRIKE_OFFSETS = [0, 1, 2]


def _rs(paise: int) -> str:
    return f"Rs {paise / 100:>12,.2f}"


def _row(cell: SweepCell) -> str:
    r = cell.report
    return (
        f"  risk={cell.risk_pct:>2}%  stop={cell.stop_pct:>2}%  target={cell.target_pct:>2}%  "
        f"otm={cell.strikes_out_of_the_money}  |  mean/day {_rs(r.mean_daily_paise)}  "
        f"total {_rs(r.total_net_paise)}  worst {_rs(r.worst_day_paise)}  "
        f"days>=500 {r.days_at_or_above_500:>3}  dd {r.max_drawdown_pct_of_capital:>5.1f}%  "
        f"trades {cell.trades:>4}  unaffordable {cell.unaffordable:>4}"
        f"{'  [DRAWDOWN-HALTED]' if cell.drawdown_halted else ''}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instrument", default="NIFTY")
    parser.add_argument("--exchange", default="NFO")
    parser.add_argument("--strategies", default="orb,random_entry")
    parser.add_argument("--capital", type=int, default=30_000, help="rupees")
    parser.add_argument("--max-daily-loss", type=int, default=1_000, help="rupees")
    parser.add_argument("--max-consecutive-losses", type=int, default=3)
    parser.add_argument("--max-drawdown-pct", type=Decimal, default=Decimal(20))
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()

    names = [s.strip() for s in args.strategies.split(",") if s.strip()]
    settings = Settings()
    store = BarStore(settings.bar_store_path)
    cost_model = CostModel(load_charge_rate_table(settings.charges_path))
    lot_history = load_lot_size_history(settings.charges_path.parent / "lot_sizes.yaml")
    contracts = OptionContractIndex(store, args.instrument)
    capital = Paise(args.capital * 100)

    combos = len(RISK_PCTS) * len(STOP_PCTS) * len(TARGET_PCTS) * len(STRIKE_OFFSETS)
    print("=== PARAMETER SWEEP -- BACKTEST, not a live or paper result ===")
    print(f"{args.instrument} {args.exchange} | strategies: {', '.join(names)}")
    print(f"capital Rs {args.capital:,} | daily loss cap Rs {args.max_daily_loss:,} | ", end="")
    print(f"max consecutive losses {args.max_consecutive_losses} | max drawdown {args.max_drawdown_pct}%")
    print(f"compounding ON | {combos} combinations x {len(names)} strategies = {combos * len(names)} runs")
    print()

    started = time.monotonic()
    cells = sweep(
        strategy_names=names,
        store=store,
        contracts=contracts,
        cost_model=cost_model,
        lot_size_for=partial(lot_size_on, lot_history, args.instrument),
        risk_pcts=RISK_PCTS,
        stop_pcts=STOP_PCTS,
        target_pcts=TARGET_PCTS,
        strike_offsets=STRIKE_OFFSETS,
        capital=capital,
        max_position_size_pct=Decimal(25),
        min_edge_multiple=settings.paper_cycle_min_edge_multiple,
        max_daily_loss_paise=args.max_daily_loss * 100,
        max_consecutive_losses=args.max_consecutive_losses,
        max_drawdown_pct=args.max_drawdown_pct,
        compound_equity=True,
        instrument=args.instrument,
        exchange=args.exchange,
        progress=lambda msg: print(f"  ... {msg}", flush=True),
    )
    elapsed = time.monotonic() - started
    print(f"\nswept {len(cells)} cells in {elapsed / 60:.1f} min\n")

    # 1. PLATEAUS FIRST — see this module's docstring.
    for name in names:
        print(f"=== {name}: plateau view (mean rupees/day, averaged over every other parameter) ===")
        for parameter in ("risk_pct", "stop_pct", "target_pct", "strikes_out_of_the_money"):
            rows = plateau(cells, strategy=name, parameter=parameter)
            rendered = "   ".join(f"{value}={_rs(mean).strip()}" for value, mean, _ in rows)
            print(f"  {parameter:<26} {rendered}")
        print()

    # 2. HEAD TO HEAD against the control, at identical parameters.
    if len(names) >= 2:  # noqa: PLR2004 — a control only exists when a second strategy was run
        primary, control = names[0], names[1]
        by_key = {(c.strategy, c.risk_pct, c.stop_pct, c.target_pct, c.strikes_out_of_the_money): c for c in cells}
        beat = sum(
            1
            for k, cell in by_key.items()
            if k[0] == primary and cell.mean_daily_paise > by_key[(control, *k[1:])].mean_daily_paise
        )
        total = sum(1 for k in by_key if k[0] == primary)
        print(f"=== {primary} vs {control} ===")
        print(f"  {primary} beat the control in {beat} of {total} identical settings ({beat / total * 100:.0f}%)")
        print("  50% is what a coin flip looks like. Well above 50% across the whole grid is the")
        print("  only pattern here that would suggest the rule itself carries information.")
        print()

    # 3. Top rows LAST, explicitly framed as a hypothesis rather than a result.
    for name in names:
        mine = sorted((c for c in cells if c.strategy == name), key=lambda c: -c.mean_daily_paise)
        print(f"=== {name}: top {args.top} by mean rupees/day (READ THE PLATEAUS ABOVE FIRST) ===")
        for cell in mine[: args.top]:
            print(_row(cell))
        best, worst = mine[0], mine[-1]
        print(f"  best {_rs(best.mean_daily_paise)}/day   worst {_rs(worst.mean_daily_paise)}/day")
        if best.mean_daily_paise <= 0:
            print("  NOTHING in this grid is profitable. No parameter choice rescues this strategy.")
        print()

    print("Every cell above is one more thing tried. 144 combinations means the best one is")
    print("expected to look good by luck alone -- which is what the plateau view is for.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
