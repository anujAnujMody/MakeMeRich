#!/usr/bin/env python
"""The other side of the trade: SELLING credit spreads, scored in rupees/day.

    python -m scripts.sell_income_report

Every strategy this project has ever tested BUYS options, and every one of them
loses. The 2026-08-04 parameter sweep put a floor under that conclusion: 144
combinations of stop, target, strike and risk, none profitable, and the gradients
all pointing at "lose more slowly" rather than at any setting that works.

There is a published reason to expect exactly that. The variance risk premium
(Carr & Wu, *Review of Financial Studies*, 2009) finds implied volatility
systematically exceeds realised on index options — which means the BUYER pays a
premium on average. It is the one options edge with strong, replicated,
peer-reviewed support. It is also entirely SPX/US, and has never been checked on
an Indian index, which is why this measures rather than assumes it.

A credit spread is the defined-risk way to be on the other side: the loss is
capped at (width - credit) by construction, so this cannot become the
unlimited-risk naked-selling trade that wipes accounts out.

### Reading rules — same as the buying sweep

Reported in rupees per day, net of all four legs' real brokerage/STT/GST, sized
against real capital, and walked through the same daily-loss / consecutive-loss
/ drawdown limits the live engine uses. `random_entry` runs alongside as the
control. Every geometry appends to the `TrialLedger`.

The grid MATCHES the buying sweep's 144 combinations on purpose. Every extra
cell tested makes the best result less trustworthy — but testing one side
thoroughly and the other lightly is worse, because it biases the comparison
toward whichever side got less scrutiny. Read the whole distribution, not the
top row.
"""

from __future__ import annotations

import argparse
import datetime as dt
import time
from decimal import Decimal
from functools import partial

from te.backtest.daily import DailyReport, build_daily_report
from te.backtest.spread_lab import SpreadGeometry, SpreadTrade, collect_signals, label_signals
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.lot_size_history import load_lot_size_history, lot_size_on
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.risk.limits import consecutive_losses_breached, daily_loss_breached, drawdown_breached
from te.settings import Settings

#: Sized to MATCH the buying sweep's 144 combinations, deliberately.
#:
#: The first draft of this file used 12 geometries. That was wrong, and the
#: reason is worth keeping: testing the buying side exhaustively (144 cells)
#: and the selling side lightly would bias the comparison toward whichever
#: side got less scrutiny. A negative result on 12 cells says "the few
#: settings I tried did not work", not "selling does not work" — and the
#: whole point here is a fair verdict on the DIRECTION of the trade.
#:
#: The knobs are genuinely different from the buying grid. A credit spread
#: has no "stop %"; it has how far out the short leg sits, how wide the
#: spread is, when profit is taken, when the loss is cut, and how close to
#: expiry it trades. Expiry band is split rather than pooled because a
#: spread expiring today and one expiring in a week are different
#: instruments — see `SpreadGeometry.min_days_to_expiry` for the measurement
#: that established that.
#:
#: 4 x 2 x 3 x 3 x 2 = 144 geometries, x 2 risk levels = 288 cells.
GEOMETRIES = [
    SpreadGeometry(
        short_otm=otm,
        width_strikes=width,
        profit_target_pct=take_profit,
        stop_loss_multiple=cut_loss,
        max_hold=dt.timedelta(hours=3),
        min_days_to_expiry=lo,
        max_days_to_expiry=hi,
    )
    for otm in (1, 2, 3, 4)
    for width in (2, 3)
    for take_profit in (Decimal("0.3"), Decimal("0.5"), Decimal("0.7"))
    for cut_loss in (Decimal("1.5"), Decimal(2), Decimal(3))
    for lo, hi in ((0, 2), (3, 7))
]
#: Applied on top of each labelled set without re-walking — risk % changes
#: only affordability and lot count, never when a spread was closed. Same
#: shortcut `te.backtest.sweep` relies on for the buying side.
RISK_PCTS = [Decimal(2), Decimal(5)]


def _rs(paise: int) -> str:
    return f"Rs {paise / 100:>11,.2f}"


def _replay(
    trades: list[SpreadTrade],
    *,
    capital: Paise,
    risk_pct: Decimal,
    max_daily_loss_paise: int,
    max_consecutive_losses: int,
    max_drawdown_pct: Decimal,
) -> tuple[DailyReport, int, int, bool]:
    """`(report, taken, unaffordable, drawdown_halted)`.

    Sizing for a defined-risk spread is simpler than for a long option: the
    maximum loss IS the risk, and it is also roughly the margin blocked, so
    one number governs both how much is at stake and what the account can
    afford to hold.
    """
    ordered = sorted(trades, key=lambda t: t.entry_ts)
    equity, peak = int(capital), int(capital)
    daily: dict[dt.date, int] = {}
    taken = unaffordable = 0
    drawdown_halted = False
    day: dt.date | None = None
    day_net = 0
    recent: list[int] = []
    day_halted = day_standdown = False

    for trade in ordered:
        on = trade.entry_ts.astimezone(IST).date()
        if on != day:
            day, day_net, recent = on, 0, []
            day_halted = day_standdown = False
        if drawdown_halted or day_halted or day_standdown:
            continue

        risk_per_lot = int(trade.max_loss_per_unit) * trade.lot_size
        if risk_per_lot <= 0:
            continue
        budget = int(Decimal(equity) * risk_pct / Decimal(100))
        lots = min(budget // risk_per_lot, equity // risk_per_lot)
        if lots < 1:
            unaffordable += 1
            continue

        net = int(trade.net_pnl_per_unit) * trade.lot_size * lots
        taken += 1
        equity += net
        day_net += net
        daily[on] = daily.get(on, 0) + net
        recent.insert(0, net)

        if daily_loss_breached(net_paise=day_net, max_daily_loss_paise=max_daily_loss_paise):
            day_halted = True
        if consecutive_losses_breached(recent_trade_nets=recent, limit=max_consecutive_losses):
            day_standdown = True
        peak = max(peak, equity)
        if equity <= 0 or drawdown_breached(
            current_equity_paise=equity, peak_equity_paise=peak, max_drawdown_pct=max_drawdown_pct
        ):
            drawdown_halted = True

    return build_daily_report(daily, capital=capital, trade_count=taken), taken, unaffordable, drawdown_halted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instrument", default="NIFTY")
    parser.add_argument("--exchange", default="NFO")
    parser.add_argument("--strategies", default="orb,random_entry")
    parser.add_argument("--capital", type=int, default=30_000)
    parser.add_argument("--max-daily-loss", type=int, default=1_000)
    parser.add_argument("--max-consecutive-losses", type=int, default=3)
    parser.add_argument("--max-drawdown-pct", type=Decimal, default=Decimal(20))
    args = parser.parse_args()

    names = [s.strip() for s in args.strategies.split(",") if s.strip()]
    settings = Settings()
    store = BarStore(settings.bar_store_path)
    cost_model = CostModel(load_charge_rate_table(settings.charges_path))
    lot_history = load_lot_size_history(settings.charges_path.parent / "lot_sizes.yaml")
    contracts = OptionContractIndex(store, args.instrument)
    capital = Paise(args.capital * 100)

    print("=== SELLING CREDIT SPREADS -- BACKTEST, not a live or paper result ===")
    print(f"{args.instrument} {args.exchange} | strategies: {', '.join(names)}")
    print(f"capital Rs {args.capital:,} | daily loss cap Rs {args.max_daily_loss:,} | ", end="")
    print(f"max drawdown {args.max_drawdown_pct}% | {len(GEOMETRIES)} geometries x {len(RISK_PCTS)} risk levels")
    print("Risk is capped by construction: max loss = (width - credit) per unit.\n")

    started = time.monotonic()
    signals = collect_signals(
        strategy_names=names,
        store=store,
        contracts=contracts,
        lot_size_for=partial(lot_size_on, lot_history, args.instrument),
        instrument=args.instrument,
    )
    print(", ".join(f"{n}: {len(s):,} signals" for n, s in signals.items()), flush=True)

    rows: list[tuple[str, SpreadGeometry, Decimal, DailyReport, int, int, bool]] = []
    for geometry in GEOMETRIES:
        for name in names:
            print(f"  ... {name} {geometry}", flush=True)
            trades, _ = label_signals(
                signals=signals[name],
                store=store,
                contracts=contracts,
                cost_model=cost_model,
                geometry=geometry,
                exchange=args.exchange,
            )
            for risk_pct in RISK_PCTS:
                report, taken, unaffordable, halted = _replay(
                    trades,
                    capital=capital,
                    risk_pct=risk_pct,
                    max_daily_loss_paise=args.max_daily_loss * 100,
                    max_consecutive_losses=args.max_consecutive_losses,
                    max_drawdown_pct=args.max_drawdown_pct,
                )
                rows.append((name, geometry, risk_pct, report, taken, unaffordable, halted))

    print(f"\nlabelled {len(rows)} cells in {(time.monotonic() - started) / 60:.1f} min\n")

    # PLATEAUS FIRST, exactly as in the buying sweep: one good cell out of 288
    # is what noise looks like; a knob VALUE that is consistently good across
    # every other setting is the only thing worth believing.
    for name in names:
        mine_rows = [r for r in rows if r[0] == name]
        print(f"=== {name}: plateau view (mean rupees/day, averaged over every other knob) ===")
        for label, key in (
            ("short leg strikes out", lambda g, r: g.short_otm),
            ("width in strikes", lambda g, r: g.width_strikes),
            ("take profit at", lambda g, r: g.profit_target_pct),
            ("cut loss at", lambda g, r: g.stop_loss_multiple),
            ("days to expiry", lambda g, r: f"{g.min_days_to_expiry}-{g.max_days_to_expiry}"),
            ("risk per trade", lambda g, r: r),
        ):
            buckets: dict[object, list[int]] = {}
            for _, geometry, risk_pct, report, *_ in mine_rows:
                buckets.setdefault(key(geometry, risk_pct), []).append(report.mean_daily_paise)
            ordered_buckets = sorted(buckets.items(), key=lambda kv: str(kv[0]))
            rendered = "   ".join(f"{value}={_rs(sum(v) // len(v)).strip()}" for value, v in ordered_buckets)
            print(f"  {label:<24} {rendered}")
        print()

    for name in names:
        mine = sorted((r for r in rows if r[0] == name), key=lambda r: -r[3].mean_daily_paise)
        print(f"=== {name}: top 15 geometries (READ THE PLATEAUS ABOVE FIRST) ===")
        mine = mine[:15]
        for _, geometry, risk_pct, report, taken, unaffordable, halted in mine:
            print(
                f"  risk={risk_pct:>2}%  {geometry!s:<34} | mean/day {_rs(report.mean_daily_paise)}  "
                f"total {_rs(report.total_net_paise)}  worst {_rs(report.worst_day_paise)}  "
                f"days>=500 {report.days_at_or_above_500:>3}  dd {report.max_drawdown_pct_of_capital:>5.1f}%  "
                f"trades {taken:>4}  unaffordable {unaffordable:>4}{'  [DD-HALTED]' if halted else ''}"
            )
        if mine and mine[0][3].mean_daily_paise <= 0:
            print("  Nothing here is profitable either.")
        print()

    print("Small grid on purpose: this is a yes/no on the DIRECTION of the trade, not a")
    print("hunt for the best cell. Every geometry tried makes every result less trustworthy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
