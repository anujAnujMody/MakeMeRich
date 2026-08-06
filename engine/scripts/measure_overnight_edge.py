#!/usr/bin/env python
"""Do index options rise overnight and fall during the day?

    python -m scripts.measure_overnight_edge
    python -m scripts.measure_overnight_edge --instrument BANKNIFTY

### Why this measurement decides the engine's shape

`te.engine.cycle` enters after the opening range completes and force-exits at
`paper_cycle_hard_exit_by` (15:15). It therefore holds risk ONLY between the
open and the close, and never across a night.

Bhat (2024), *Journal of Futures Markets*, on Indian index options reports the
opposite of what that design assumes: "positive and significant overnight
option returns are accompanied by negative intraday returns", robust across
option categories, and concludes the variance risk premium option sellers earn
is "mainly a reward for overnight risk".

If that holds on OUR data, then every intraday-only result this project has
produced — 288 sweep cells, 32 strategies, all clustering around zero — was
measured inside the one window where the published edge is absent. That would
be a finding about the engine's design, not about its parameters.

This script does not trade anything. It measures two returns per contract per
session and reports the distribution. Nothing here is a strategy.

### What is measured, precisely

For one option contract, on consecutive recorded sessions N and N+1:

    overnight = first OPEN of session N+1  /  last CLOSE of session N   - 1
    intraday  = last CLOSE of session N    /  first OPEN of session N   - 1

Both are the **option BUYER's** return. A positive overnight number means
premium rose while the market was shut — good for whoever was long, bad for
whoever was short.

### Honest limits, stated rather than discovered later

* **Gross of costs.** A round trip pays real brokerage/STT/GST, and on a cheap
  premium that is a large percentage. The mean returns below are what the
  PRICE did, not what an account would have kept. `te.domain.costs` prices the
  real thing; this measurement deliberately does not, so the raw asymmetry is
  visible before costs are argued about.
* **No bid-ask.** Bars are traded prices. Crossing the spread twice is real
  and is not modelled here.
* **Survivorship is not an issue but LIQUIDITY is.** Every recorded contract is
  included, including strikes that barely traded, whose "open" and "close" may
  be minutes or hours apart. `--min-bars` sets a floor on how many minutes a
  contract must have traded in a session to count, and the result is reported
  at several floors so the reader can see whether the effect is an artefact of
  thin strikes.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from te.domain.clock import IST  # noqa: E402
from te.domain.symbols import parse_option_symbol  # noqa: E402
from te.settings import Settings  # noqa: E402


def _session_extremes(root: Path, instrument: str, min_bars: int) -> pd.DataFrame:
    """One row per (symbol, session): first open, last close, bar count.

    Read month-partition by month-partition. The NIFTY archive alone is ~165M
    bars; materialising it whole is neither necessary nor survivable, and the
    aggregation collapses each contract-session to a single row.
    """
    interval_root = root / "interval=1m"
    if not interval_root.exists():
        raise SystemExit(f"no bar store at {interval_root}")

    frames: list[pd.DataFrame] = []
    symbol_dirs = sorted(p for p in interval_root.iterdir() if p.name.startswith("symbol="))
    wanted = []
    for path in symbol_dirs:
        symbol = path.name.split("=", 1)[1]
        try:
            parsed = parse_option_symbol(symbol)
        except ValueError:
            continue  # index/spot series, not an option contract
        if parsed.base != instrument:
            continue
        wanted.append((symbol, path))

    if not wanted:
        raise SystemExit(f"no {instrument} option contracts in {interval_root}")

    print(f"{instrument}: {len(wanted):,} option contracts in the archive", flush=True)

    for index, (symbol, path) in enumerate(wanted, start=1):
        if index % 2000 == 0:
            print(f"  ... {index:,}/{len(wanted):,}", flush=True)
        table = ds.dataset(path, format="parquet", partitioning="hive").to_table(
            columns=["event_ts", "o", "c"]
        )
        if table.num_rows == 0:
            continue
        frame = table.to_pandas()
        frame["session"] = frame["event_ts"].dt.tz_convert(IST).dt.date
        frame = frame.sort_values("event_ts")
        grouped = frame.groupby("session", sort=True)
        agg = pd.DataFrame(
            {
                "first_open": grouped["o"].first(),
                "last_close": grouped["c"].last(),
                "bars": grouped.size(),
            }
        ).reset_index()
        agg["symbol"] = symbol
        agg = agg[agg["bars"] >= min_bars]
        if not agg.empty:
            frames.append(agg)

    if not frames:
        raise SystemExit("no contract-sessions survived the bar-count floor")
    return pd.concat(frames, ignore_index=True)


def _straddles(extremes: pd.DataFrame) -> pd.DataFrame:
    """Collapse CE/PE at the same (expiry, strike, session) into ONE synthetic
    straddle premium.

    This is the load-bearing correction. Measured per contract, the first
    BANKNIFTY run showed calls falling and puts rising over the same nights —
    which is the index drifting, not a volatility premium, and averaging the
    two produced a headline that meant nothing. A straddle is delta-neutral at
    inception, so a directional move raises one leg and lowers the other and
    largely cancels. What survives is what happened to PREMIUM, which is the
    only thing this measurement is about.
    """
    parsed = extremes["symbol"].map(parse_option_symbol)
    frame = extremes.assign(
        expiry=[p.expiry for p in parsed],
        strike=[p.strike for p in parsed],
        option_type=[p.option_type for p in parsed],
    )
    pivot = frame.pivot_table(
        index=["expiry", "strike", "session"],
        columns="option_type",
        values=["first_open", "last_close", "bars"],
        aggfunc="first",
    )
    # Both legs must exist and both must have traded — a straddle priced off
    # one real leg and one stale one is not delta-neutral.
    needed = [("first_open", "CE"), ("first_open", "PE"), ("last_close", "CE"), ("last_close", "PE")]
    if any(c not in pivot.columns for c in needed):
        return pd.DataFrame()
    pivot = pivot.dropna(subset=needed)
    out = pd.DataFrame(
        {
            "first_open": pivot[("first_open", "CE")] + pivot[("first_open", "PE")],
            "last_close": pivot[("last_close", "CE")] + pivot[("last_close", "PE")],
            "bars": pivot[("bars", "CE")].combine(pivot[("bars", "PE")], min),
        }
    ).reset_index()
    out["symbol"] = out["expiry"].astype(str) + "-" + out["strike"].astype(str)
    return out


def _returns(extremes: pd.DataFrame, min_premium_rupees: float) -> pd.DataFrame:
    """Overnight and intraday BUYER returns, per contract per session."""
    out: list[dict[str, object]] = []
    for symbol, rows in extremes.groupby("symbol", sort=False):
        rows = rows.sort_values("session").reset_index(drop=True)
        for i in range(len(rows)):
            today = rows.iloc[i]
            if today["first_open"] >= min_premium_rupees and today["first_open"] > 0:
                out.append(
                    {
                        "symbol": symbol,
                        "session": today["session"],
                        "kind": "intraday",
                        "ret": today["last_close"] / today["first_open"] - 1.0,
                    }
                )
            if i == 0:
                continue
            prev = rows.iloc[i - 1]
            # Consecutive RECORDED sessions only. A gap (holiday, or a strike
            # that simply did not trade for days) is not an overnight hold, and
            # counting it as one would fold multi-day moves into a one-night
            # number.
            if (today["session"] - prev["session"]).days > 3:
                continue
            if prev["last_close"] < min_premium_rupees or prev["last_close"] <= 0:
                continue
            out.append(
                {
                    "symbol": symbol,
                    "session": today["session"],
                    "kind": "overnight",
                    "ret": today["first_open"] / prev["last_close"] - 1.0,
                }
            )
    return pd.DataFrame(out)


def _report(returns: pd.DataFrame, label: str) -> None:
    print(f"\n=== {label} ===")
    print(f"{'window':<12}{'n':>10}{'mean %':>10}{'median %':>10}{'% > 0':>9}{'std %':>9}")
    for kind in ("overnight", "intraday"):
        block = returns[returns["kind"] == kind]["ret"]
        if block.empty:
            print(f"{kind:<12}{0:>10}{'-':>10}{'-':>10}{'-':>9}{'-':>9}")
            continue
        print(
            f"{kind:<12}{len(block):>10,}{block.mean() * 100:>10.3f}"
            f"{block.median() * 100:>10.3f}{(block > 0).mean() * 100:>8.1f}%"
            f"{block.std() * 100:>9.2f}"
        )
    on = returns[returns["kind"] == "overnight"]["ret"]
    day = returns[returns["kind"] == "intraday"]["ret"]
    if not on.empty and not day.empty:
        print(f"\n  overnight minus intraday mean: {(on.mean() - day.mean()) * 100:+.3f}% per session")
        print("  (positive = premium rises overnight and falls in the day, i.e. the")
        print("   buyer is paid at night and the seller is paid in the day)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instrument", default="NIFTY")
    parser.add_argument(
        "--min-bars",
        type=int,
        default=30,
        help="minutes a contract must have traded in a session for it to count",
    )
    parser.add_argument(
        "--min-premium",
        type=float,
        default=5.0,
        help="in RUPEES: ignore contract-sessions starting below this, where a one-tick move is a huge percentage",
    )
    args = parser.parse_args()

    settings = Settings()
    print("=== OVERNIGHT vs INTRADAY option returns — MEASUREMENT, not a backtest ===")
    print(f"instrument={args.instrument}  min_bars={args.min_bars}  min_premium=Rs {args.min_premium}")
    print("Returns are the option BUYER's, GROSS of brokerage/STT/GST and of the bid-ask spread.\n")

    extremes = _session_extremes(settings.bar_store_path, args.instrument, args.min_bars)
    print(f"\ncontract-sessions: {len(extremes):,}")
    print(f"date range: {extremes['session'].min()} .. {extremes['session'].max()}")

    straddles = _straddles(extremes)
    if straddles.empty:
        print("no strike had BOTH legs recorded — cannot form a straddle")
    else:
        print(f"straddles (both legs traded): {len(straddles):,} strike-sessions")
        _report(_returns(straddles, args.min_premium), f"{args.instrument} STRADDLE (direction cancelled)")

    returns = _returns(extremes, args.min_premium)
    _report(returns, f"{args.instrument} — single legs, DIAGNOSTIC ONLY")

    # Split by option type: a directional drift in the underlying would show
    # up as calls and puts moving OPPOSITE ways, which is not a volatility
    # effect at all. If both sides share the sign, that is the premium moving.
    kinds = defaultdict(list)
    for symbol in returns["symbol"].unique():
        try:
            kinds[parse_option_symbol(symbol).option_type].append(symbol)
        except ValueError:
            continue
    for option_type, symbols in sorted(kinds.items()):
        _report(returns[returns["symbol"].isin(symbols)], f"{args.instrument} {option_type} only")

    print("\nRead the CE/PE split before believing the headline: if calls and puts")
    print("move the same way, that is premium; if they move opposite ways, it is")
    print("just the index drifting and says nothing about a volatility premium.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
