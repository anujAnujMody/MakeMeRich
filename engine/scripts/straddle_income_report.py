#!/usr/bin/env python
"""The only structure our own data says decays: sell the straddle, scored in
rupees/day.

    python -m scripts.straddle_income_report
    python -m scripts.straddle_income_report --instrument BANKNIFTY --capital 50000

### Why this run exists

`scripts.measure_overnight_edge` measured, over 72,518 NIFTY strike-sessions,
that a straddle loses a median 1.674% of its premium between the open and the
close while the overnight window is flat. That is the one positive measurement
this project has. Everything tested since — 32 strategies, a 144-cell buying
sweep, a 288-cell credit-spread sweep — tested a DIRECTIONAL bet instead, and
all of it lost.

This sells the structure the decay was actually measured on, with wings, at a
fixed clock time, with no signal of any kind. See `te.backtest.straddle_lab`
for why the wings are mandatory at this capital and what that costs.

### The prior is negative — read the year-by-year table before the average

In India this is the well-known "9:20 straddle", and the practitioner record
says it stopped working: Marketcalls reports 0.9 points per trade over 1.5
years and 36% accuracy, unprofitable after costs, with crowding and the
post-2020 volatility regime as the usual explanations. If our data shows a
strong five-year average driven entirely by 2024 and earlier, that is the same
finding, not a contradiction of it — which is why the per-year breakdown is
printed for every cell and is not optional.

Entry time is swept rather than fixed at 9:20 for the same reason: an edge that
exists at 9:20 and vanishes at 9:25 is crowding or noise. Plateaus, not peaks.
"""

from __future__ import annotations

import argparse
import datetime as dt
import time
from decimal import Decimal
from functools import partial

from te.backtest.daily import DailyReport, build_daily_report
from te.backtest.straddle_lab import (
    StraddleGeometry,
    StraddleTrade,
    collect_positions,
    label_positions,
)
from te.backtest.strategy_lab import _WholeSymbolCache
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.lot_size_history import load_lot_size_history, lot_size_on
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.risk.limits import consecutive_losses_breached, daily_loss_breached, drawdown_breached
from te.settings import Settings

#: 4 entry times x 3 wing widths x 4 stops x 3 targets x 2 expiry bands = 288
#: geometries, matching the buying and credit-spread sweeps cell for cell so
#: no side of the comparison gets less scrutiny than another.
#:
#: The values are the published practitioner figures, not invented ones:
#: 9:20 is the canonical entry, 25-30% is the standard per-leg stop, and
#: "no stop" is included because the same sources disagree about whether the
#: stop helps or simply guarantees the loss on a V-shaped day.
ENTRY_TIMES = (dt.time(9, 20), dt.time(9, 45), dt.time(10, 15), dt.time(11, 0))
WING_POINTS = (Decimal(100), Decimal(200), Decimal(300))
LEG_STOPS: tuple[Decimal | None, ...] = (Decimal("0.25"), Decimal("0.30"), Decimal("0.50"), None)
TAKE_PROFITS: tuple[Decimal | None, ...] = (Decimal("0.3"), Decimal("0.5"), None)
EXPIRY_BANDS = ((0, 2), (3, 7))

GEOMETRIES = [
    StraddleGeometry(
        entry_time=entry_time,
        wing_points=wing,
        leg_stop_fraction=stop,
        take_profit_fraction=take,
        min_days_to_expiry=lo,
        max_days_to_expiry=hi,
    )
    for entry_time in ENTRY_TIMES
    for wing in WING_POINTS
    for stop in LEG_STOPS
    for take in TAKE_PROFITS
    for lo, hi in EXPIRY_BANDS
]


def _rs(paise: int) -> str:
    return f"Rs {paise / 100:>11,.2f}"


def _replay(
    trades: list[StraddleTrade],
    *,
    capital: Paise,
    risk_pct: Decimal,
    max_daily_loss_paise: int,
    max_consecutive_losses: int,
    max_drawdown_pct: Decimal,
) -> tuple[DailyReport, int, int, bool, dict[int, int]]:
    """`(report, taken, unaffordable, drawdown_halted, net_by_year)`.

    Sized off `max_loss_per_unit`, which for a defined-risk structure is both
    the money at stake and a close proxy for the margin blocked — one number
    governing both what is risked and what the account can hold.

    `net_by_year` is what makes the crowding question answerable: a strategy
    that worked until 2023 and stopped averages out to something mildly
    positive over five years, and the average alone would hide that.
    """
    ordered = sorted(trades, key=lambda t: t.entry_ts)
    equity, peak = int(capital), int(capital)
    daily: dict[dt.date, int] = {}
    by_year: dict[int, int] = {}
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
        by_year[on.year] = by_year.get(on.year, 0) + net
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

    return (
        build_daily_report(daily, capital=capital, trade_count=taken),
        taken,
        unaffordable,
        drawdown_halted,
        by_year,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instrument", default="NIFTY")
    parser.add_argument("--exchange", default="NFO")
    parser.add_argument("--capital", type=int, default=50_000)
    parser.add_argument("--max-daily-loss", type=int, default=2_000)
    parser.add_argument("--max-consecutive-losses", type=int, default=3)
    parser.add_argument("--max-drawdown-pct", type=Decimal, default=Decimal(20))
    parser.add_argument("--risk-pcts", default="3,5")
    args = parser.parse_args()

    risk_pcts = [Decimal(p.strip()) for p in args.risk_pcts.split(",") if p.strip()]
    settings = Settings()
    store = BarStore(settings.bar_store_path)
    cost_model = CostModel(load_charge_rate_table(settings.charges_path))
    lot_history = load_lot_size_history(settings.charges_path.parent / "lot_sizes.yaml")
    contracts = OptionContractIndex(store, args.instrument)
    capital = Paise(args.capital * 100)

    print("=== SHORT STRADDLE WITH WINGS (IRON FLY) -- BACKTEST, not a live or paper result ===")
    print(f"{args.instrument} {args.exchange} | no directional signal — fixed entry time only")
    print(f"capital Rs {args.capital:,} | daily loss cap Rs {args.max_daily_loss:,} | ", end="")
    print(f"max drawdown {args.max_drawdown_pct}% | {len(GEOMETRIES)} geometries x {len(risk_pcts)} risk levels")
    print("Risk capped by construction: max loss = (BOTH wing distances - credit) per unit,")
    print("measured from the strikes actually resolved. It is GROSS of charges.")
    print("Published prior is NEGATIVE (the '9:20 straddle' is widely reported dead) — read")
    print("the per-year table before the five-year average.\n")

    started = time.monotonic()
    rows: list[tuple[StraddleGeometry, Decimal, DailyReport, int, int, int, bool, dict[int, int]]] = []
    # Bars are read ONCE per (entry time, wing, expiry band) — 24 sets for
    # the whole 288-cell grid — and every stop/target combination sharing
    # those three is then pure arithmetic over the cached series. See
    # `SessionPosition`. `_WholeSymbolCache` removes the repeated Parquet
    # opens underneath that.
    cached_store = _WholeSymbolCache(store.root, store)
    position_sets = [
        (entry_time, wing, band)
        for entry_time in ENTRY_TIMES
        for wing in WING_POINTS
        for lo, hi in EXPIRY_BANDS
        for band in ((lo, hi),)
    ]
    for entry_time, wing, (lo, hi) in position_sets:
        shape = StraddleGeometry(
            entry_time=entry_time, wing_points=wing, leg_stop_fraction=None, take_profit_fraction=None,
            min_days_to_expiry=lo, max_days_to_expiry=hi,
        )
        positions, unresolved = collect_positions(
            store=cached_store,
            contracts=contracts,
            lot_size_for=partial(lot_size_on, lot_history, args.instrument),
            geometry=shape,
            instrument=args.instrument,
        )
        top = ", ".join(f"{reason}={n}" for reason, n in unresolved.most_common(3))
        print(
            f"  ... {entry_time:%H:%M} wing{wing} dte{lo}-{hi}: "
            f"{len(positions):>4} positions, {sum(unresolved.values()):>4} unresolved"
            f"{'  [' + top + ']' if top else ''}",
            flush=True,
        )
        for geometry in GEOMETRIES:
            if (geometry.entry_time, geometry.wing_points, geometry.min_days_to_expiry) != (entry_time, wing, lo):
                continue
            trades, unlabelled = label_positions(
                positions=positions, geometry=geometry, cost_model=cost_model, exchange=args.exchange
            )
            for risk_pct in risk_pcts:
                report, taken, unaffordable, halted, by_year = _replay(
                    trades,
                    capital=capital,
                    risk_pct=risk_pct,
                    max_daily_loss_paise=args.max_daily_loss * 100,
                    max_consecutive_losses=args.max_consecutive_losses,
                    max_drawdown_pct=args.max_drawdown_pct,
                )
                rows.append(
                    (
                        geometry, risk_pct, report, taken, unaffordable,
                        sum((unresolved + unlabelled).values()), halted, by_year,
                    )
                )

    print(f"\nlabelled {len(rows)} cells in {(time.monotonic() - started) / 60:.1f} min\n")

    print("=== plateau view (mean rupees/day, averaged over every other knob) ===")
    for label, key in (
        ("entry time", lambda g, r: f"{g.entry_time:%H:%M}"),
        ("wing distance", lambda g, r: f"{g.wing_points}pt"),
        ("per-leg stop", lambda g, r: "none" if g.leg_stop_fraction is None else f"{g.leg_stop_fraction}"),
        ("take profit at", lambda g, r: "none" if g.take_profit_fraction is None else str(g.take_profit_fraction)),
        ("days to expiry", lambda g, r: f"{g.min_days_to_expiry}-{g.max_days_to_expiry}"),
        ("risk per trade", lambda g, r: f"{r}%"),
    ):
        # ONLY cells that actually traded. Averaging a zero-session cell in
        # as a zero drags every plateau toward Rs 0.00 and makes a knob
        # value that never traded look merely mediocre. The contributing
        # cell count is printed beside each value so a bucket resting on two
        # cells cannot be read as one resting on twenty-four.
        buckets: dict[object, list[int]] = {}
        for geometry, risk_pct, report, *_ in rows:
            if report.sessions == 0:
                continue
            buckets.setdefault(key(geometry, risk_pct), []).append(report.mean_daily_paise)
        rendered = "   ".join(
            f"{value}={_rs(sum(v) // len(v)).strip()}(n={len(v)})"
            for value, v in sorted(buckets.items(), key=lambda kv: str(kv[0]))
        ) or "no cell traded"
        print(f"  {label:<18} {rendered}")

    years = sorted({year for *_, by_year in rows for year in by_year})
    print("\n=== per YEAR (mean rupees/day within that year, averaged over every cell) ===")
    print("  A five-year average is not a result if it is one good year and four flat ones.")
    for year in years:
        values = [by_year[year] for *_, by_year in rows if year in by_year]
        print(f"  {year}  mean per cell {_rs(sum(values) // max(1, len(values)))}  (cells trading: {len(values)})")

    # A minimum sample before a cell may be RANKED. Without it the table is
    # sorted by an unnormalised mean over incomparable sample sizes, and a
    # cell that traded twice sits above one that traded six hundred times —
    # exactly what the first run produced (14 of its top 15 rows had 1-2
    # trades). Excluded cells are counted, never silently dropped.
    min_sessions_to_rank = 30
    rankable = [r for r in rows if r[2].sessions >= min_sessions_to_rank]
    print(f"\n=== top 15 cells with at least {min_sessions_to_rank} trading days ===")
    print(f"  {len(rows) - len(rankable)} of {len(rows)} cells excluded for too small a sample.")
    for geometry, risk_pct, report, taken, unaffordable, unresolved, halted, by_year in sorted(
        rankable, key=lambda r: -r[2].mean_daily_paise
    )[:15]:
        per_year = " ".join(f"{y}:{v // 100:+,}" for y, v in sorted(by_year.items()))
        print(
            f"  risk={risk_pct:>2}%  {geometry!s:<44} | mean/day {_rs(report.mean_daily_paise)}  "
            f"total {_rs(report.total_net_paise)}  worst {_rs(report.worst_day_paise)}  "
            f"dd {report.max_drawdown_pct_of_capital:>5.1f}%  trades {taken:>4}  "
            f"unaffordable {unaffordable:>4}  unresolved {unresolved:>4}{'  [DD-HALTED]' if halted else ''}"
        )
        print(f"         by year: {per_year}")

    best = max((r[2].mean_daily_paise for r in rankable), default=0)
    if best <= 0:
        print("\nNo cell is profitable. That is the answer, not a reason to widen the grid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
