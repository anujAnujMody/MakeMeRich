#!/usr/bin/env python
"""Does OPEN INTEREST predict anything?

Usage:
    python -m scripts.measure_oi_edge
    python -m scripts.measure_oi_edge --instruments NIFTY --min-days 300

### Why this exists

Every entry rule tried so far has lost to friction, and a random coin flip
matched the best of them (`scripts/sweep_strategies.py`). That result says
the ENTRY TIMING carries no information. It says nothing about whether some
other variable can tell a good day from a bad one — and open interest is the
obvious candidate, because it is the one genuinely different piece of
information available: not price, but POSITIONING.

It is also free. `option_bhav` has been fed by the daily NSE/BSE bhavcopy
job since Phase 1 and carries `open_interest`/`change_in_oi` per strike per
day, across six underlyings — and until now nothing in the codebase read it.
Testing it costs nothing, which is the right thing to do before paying a
vendor for the minute-level version of the same variable.

### What is measured

For each trading day, OI features are built from that day's END-OF-DAY chain
— published after the close, so they are legitimately known before the NEXT
session — and tested against what the index did the FOLLOWING day.

Three targets, because they fail differently and only the first is about
direction:

* **close-to-close return** — the textbook test.
* **open-to-close return** — what an intraday strategy could actually
  capture, since we are flat overnight.
* **absolute open-to-close move** — does OI predict how far price travels,
  regardless of which way? This one matters even if direction is hopeless:
  a bought option needs MOVEMENT, so a variable that flags big-range days
  would be a real position-sizing input even with no directional content.

### Honesty about the size of this search

Several features x several targets x several underlyings is dozens of
trials, and the best of dozens of pure-noise draws lands around 2.5 sigma by
construction. A Blom best-of-N floor is therefore printed and every result
is judged against it, exactly as in `sweep_strategies.py`. Nothing here is
out-of-sample.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import pandas as pd
import sqlalchemy as sa

from te.data.barstore import BarStore
from te.domain.clock import IST
from te.ml.metrics import expected_max_z
from te.settings import Settings

#: Underlyings that have BOTH an option chain in `option_bhav` and index
#: bars to measure the outcome on. FINNIFTY/MIDCPNIFTY have chains but no
#: recorded bars, so they are excluded rather than silently half-measured.
DEFAULT_INSTRUMENTS = ("NIFTY", "BANKNIFTY", "SENSEX", "BANKEX")


@dataclass(frozen=True)
class DayFeatures:
    day: dt.date
    spot: float
    features: dict[str, float]


def _chain_for_day(frame: pd.DataFrame, spot: float) -> dict[str, float] | None:
    """OI features from ONE day's option chain.

    Restricted to the NEAREST expiry that actually carries open interest.
    Pooling every listed expiry would let a thin far-dated series move a
    ratio that is meant to describe where the crowd is positioned right now,
    and the near expiry is where essentially all index-option OI sits.
    """
    live = frame[frame["open_interest"] > 0]
    if live.empty:
        return None
    expiry = live["expiry"].min()
    chain = live[live["expiry"] == expiry]
    calls = chain[chain["option_type"] == "CE"]
    puts = chain[chain["option_type"] == "PE"]
    if calls.empty or puts.empty or not spot:
        return None

    call_oi = float(calls["open_interest"].sum())
    put_oi = float(puts["open_interest"].sum())
    call_vol = float(calls["volume"].sum())
    put_vol = float(puts["volume"].sum())
    if call_oi <= 0:
        return None

    # Max pain: the strike at which option BUYERS collectively receive the
    # least. Computed over the strikes that actually exist rather than a
    # synthetic grid, so it can only land somewhere really traded.
    strikes = np.sort(chain["strike"].unique())
    call_strikes = calls["strike"].to_numpy()
    call_oi_arr = calls["open_interest"].to_numpy(dtype=float)
    put_strikes = puts["strike"].to_numpy()
    put_oi_arr = puts["open_interest"].to_numpy(dtype=float)
    pain = [
        float(
            (call_oi_arr * np.maximum(0.0, k - call_strikes)).sum()
            + (put_oi_arr * np.maximum(0.0, put_strikes - k)).sum()
        )
        for k in strikes
    ]
    max_pain = float(strikes[int(np.argmin(pain))])

    # The heaviest single call/put strike — the crowd's "wall", read as
    # resistance above and support below in the standard interpretation.
    call_wall = float(calls.loc[calls["open_interest"].idxmax(), "strike"])
    put_wall = float(puts.loc[puts["open_interest"].idxmax(), "strike"])

    return {
        "pcr_oi": put_oi / call_oi,
        "pcr_volume": put_vol / call_vol if call_vol > 0 else float("nan"),
        "max_pain_distance_pct": (max_pain - spot) / spot * 100,
        "call_wall_distance_pct": (call_wall - spot) / spot * 100,
        "put_wall_distance_pct": (put_wall - spot) / spot * 100,
        "total_oi": call_oi + put_oi,
        "oi_concentration": float(chain["open_interest"].max()) / (call_oi + put_oi),
        "net_oi_change": float(chain["change_in_oi"].sum()) / (call_oi + put_oi),
        "days_to_expiry": float((expiry - frame["trade_date"].iloc[0]).days),
    }


def _daily_index(store: BarStore, symbol: str) -> pd.DataFrame:
    """Per-session open/close/high/low for the index, keyed by IST date."""
    frame = store.read(
        symbol=symbol,
        start=dt.datetime(2000, 1, 1, tzinfo=dt.UTC),
        end=dt.datetime(2100, 1, 1, tzinfo=dt.UTC),
        interval="1m",
    )
    if frame.empty:
        return pd.DataFrame()
    frame = frame.drop_duplicates(subset="event_ts", keep="last").sort_values("event_ts")
    ist = frame["event_ts"].dt.tz_convert(IST)
    grouped = frame.groupby(ist.dt.date)
    return pd.DataFrame(
        {
            "open": grouped["o"].first(),
            "close": grouped["c"].last(),
            "high": grouped["h"].max(),
            "low": grouped["l"].min(),
        }
    )


def _load(engine: sa.Engine, symbol: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql_query(
            sa.text(
                "SELECT trade_date, expiry, strike, option_type, open_interest, change_in_oi, volume "
                "FROM option_bhav WHERE symbol = :s"
            ),
            conn,
            params={"s": symbol},
            parse_dates=["trade_date", "expiry"],
        )


def _spearman_t(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Rank correlation and its t-statistic.

    RANK-based on purpose: OI ratios have long tails and a couple of expiry
    days with freak values would otherwise dominate a Pearson correlation and
    manufacture significance out of two data points.
    """
    n = len(x)
    if n < 30:
        return float("nan"), float("nan")
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    rho = float(np.corrcoef(rx, ry)[0, 1])
    if abs(rho) >= 1.0:
        return rho, float("inf")
    t_stat = rho * math.sqrt((n - 2) / (1 - rho**2))
    return rho, t_stat


def _tercile_spread(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Mean outcome in the top third of the feature minus the bottom third,
    with a two-sample t-statistic. Reported beside the correlation because a
    monotonic relationship and a usable one are different things — a rule
    trades the extremes, not the average."""
    if len(x) < 60:
        return float("nan"), float("nan")
    low_cut, high_cut = np.quantile(x, 1 / 3), np.quantile(x, 2 / 3)
    low, high = y[x <= low_cut], y[x >= high_cut]
    if len(low) < 15 or len(high) < 15:
        return float("nan"), float("nan")
    spread = float(high.mean() - low.mean())
    se = math.sqrt(high.var(ddof=1) / len(high) + low.var(ddof=1) / len(low))
    return spread, (spread / se if se > 0 else float("nan"))


def _residualise(y: np.ndarray, controls: list[np.ndarray]) -> np.ndarray:
    """`y` with the linear influence of `controls` removed."""
    design = np.column_stack([np.ones_like(y), *controls])
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    return y - design @ beta


def _partial_spearman_t(
    x: np.ndarray, y: np.ndarray, controls: list[np.ndarray]
) -> tuple[float, float]:
    """Rank correlation between `x` and `y` AFTER removing `controls` from both.

    This is the test that decides whether the open-interest result is real or
    an illusion, and it is not optional.

    Volatility clusters — a wild day is followed by a wild day, which is one
    of the oldest and most robust facts in finance. Total open interest and
    OI concentration both move with recent activity, so a raw correlation
    between them and TOMORROW's range could be measuring nothing more than
    "yesterday was volatile". That would be a free signal available from
    price alone, and paying a vendor for open-interest data to rediscover it
    would be a waste.

    Removing the volatility controls from BOTH sides (rather than only from
    the outcome) is what makes this a genuine partial correlation: what
    survives is the part of OI unexplained by recent volatility, against the
    part of tomorrow's range unexplained by it.
    """
    if len(x) < 60:
        return float("nan"), float("nan")
    return _spearman_t(_residualise(x, controls), _residualise(y, controls))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instruments", default=",".join(DEFAULT_INSTRUMENTS))
    parser.add_argument("--min-days", type=int, default=100)
    parser.add_argument(
        "--split-date",
        type=dt.date.fromisoformat,
        default=dt.date(2025, 9, 1),
        help="walk-forward boundary; days before it are the early period, days on/after are the late one",
    )
    args = parser.parse_args()

    settings = Settings()
    store = BarStore(settings.bar_store_path)
    engine = sa.create_engine(settings.database_url)
    chosen = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]

    print("Does END-OF-DAY open interest predict the NEXT session?")
    print("Features come from the chain published after day D's close; outcomes are day D+1.")
    print("No costs, no options traded — this asks whether the INFORMATION exists.\n")

    results: list[tuple[str, str, str, int, float, float, float]] = []
    #: symbol -> (paired days, features, targets), kept so the walk-forward
    #: below re-uses the exact same rows rather than rebuilding them and
    #: risking a subtly different sample.
    per_symbol: dict[str, tuple[list[dt.date], dict[str, list[float]], dict[str, list[float]]]] = {}
    for symbol in chosen:
        chain = _load(engine, symbol)
        index = _daily_index(store, symbol)
        if chain.empty or index.empty:
            print(f"{symbol}: no chain or no index bars — skipped", file=sys.stderr)
            continue
        chain["trade_date"] = chain["trade_date"].dt.date
        chain["expiry"] = chain["expiry"].dt.date

        per_day: list[DayFeatures] = []
        for day, frame in chain.groupby("trade_date", sort=True):
            if day not in index.index:
                continue
            spot = float(index.loc[day, "close"])
            features = _chain_for_day(frame, spot)
            if features is not None:
                per_day.append(DayFeatures(day=day, spot=spot, features=features))

        if len(per_day) < args.min_days:
            print(f"{symbol}: only {len(per_day)} usable days (need {args.min_days}) — skipped", file=sys.stderr)
            continue

        # Day D's features are matched to day D+1's outcome. The shift is the
        # entire point-in-time discipline of this script: pairing a day's
        # chain with its OWN session would be reading the answer.
        days = [d.day for d in per_day]
        available = [d for d in index.index if d in set(days)]
        order = {d: i for i, d in enumerate(sorted(available))}
        del order

        rows: dict[str, list[float]] = defaultdict(list)
        targets: dict[str, list[float]] = defaultdict(list)
        controls: dict[str, list[float]] = defaultdict(list)
        paired_days: list[dt.date] = []
        index_dates = sorted(index.index)
        position = {d: i for i, d in enumerate(index_dates)}
        # Each session's own absolute open-to-close move, used below as the
        # volatility-clustering control. Computed once for the whole series
        # so the trailing window is a real trailing window.
        daily_abs = {
            d: abs(float(index.loc[d, "close"]) - float(index.loc[d, "open"])) / float(index.loc[d, "open"]) * 10_000
            for d in index_dates
        }
        for record in per_day:
            i = position.get(record.day)
            if i is None or i + 1 >= len(index_dates):
                continue
            nxt = index_dates[i + 1]
            nxt_open = float(index.loc[nxt, "open"])
            nxt_close = float(index.loc[nxt, "close"])
            today_close = float(index.loc[record.day, "close"])
            # Controls use days up to and INCLUDING D — never D+1 — so they
            # are known at the same moment the OI features are.
            trailing = [daily_abs[d] for d in index_dates[max(0, i - 4) : i + 1]]
            for name, value in record.features.items():
                rows[name].append(value)
            paired_days.append(record.day)
            controls["prior_day_abs_bps"].append(daily_abs[record.day])
            controls["trailing_5d_abs_bps"].append(float(np.mean(trailing)))
            targets["close_to_close_bps"].append((nxt_close - today_close) / today_close * 10_000)
            targets["open_to_close_bps"].append((nxt_close - nxt_open) / nxt_open * 10_000)
            targets["abs_open_to_close_bps"].append(abs(nxt_close - nxt_open) / nxt_open * 10_000)

        n = len(targets["close_to_close_bps"])
        per_symbol[symbol] = (paired_days, dict(rows), dict(targets))
        print(f"=== {symbol} ===  {n:,} paired sessions, {per_day[0].day} .. {per_day[-1].day}")
        for feature_name, values in rows.items():
            x = np.asarray(values, dtype=float)
            for target_name, target_values in targets.items():
                y = np.asarray(target_values, dtype=float)
                keep = ~np.isnan(x) & ~np.isnan(y)
                if keep.sum() < args.min_days:
                    continue
                rho, t_rho = _spearman_t(x[keep], y[keep])
                control_arrays = [np.asarray(controls[c], dtype=float)[keep] for c in sorted(controls)]
                _, t_partial = _partial_spearman_t(x[keep], y[keep], control_arrays)
                results.append((symbol, feature_name, target_name, int(keep.sum()), rho, t_rho, t_partial))
        print()

    if not results:
        print("no results — is `option_bhav` populated? see scripts/backfill_bhavcopy.py", file=sys.stderr)
        return 2

    floor = expected_max_z(len(results))
    header = f"{'instrument':<11}{'feature':<24}{'target':<24}{'n':>7}{'rho':>8}{'t(raw)':>9}{'t(vs vol)':>11}  verdict"
    print(header)
    for symbol, feature, target, n, rho, t_rho, t_partial in sorted(results, key=lambda r: -abs(r[5])):
        # A result must survive BOTH the multiple-comparison floor and the
        # volatility control. Clearing only the first means it is probably
        # just volatility clustering wearing an open-interest costume.
        if abs(t_rho) > floor and abs(t_partial) > floor:
            verdict = "SURVIVES BOTH"
        elif abs(t_rho) > floor:
            verdict = "explained by volatility clustering"
        else:
            verdict = ""
        partial_text = f"{t_partial:>+11.2f}" if not math.isnan(t_partial) else f"{'n/a':>11}"
        print(f"{symbol:<11}{feature:<24}{target:<24}{n:>7,}{rho:>+8.3f}{t_rho:>+9.2f}{partial_text}  {verdict}")

    print(f"\n{len(results)} tests run -> |t| must exceed {floor:.2f} to beat the best-of-{len(results)} noise floor.")
    print("rho is rank correlation: +1 perfectly ordered, 0 no relationship.")
    print("t(raw) is the plain result. t(vs vol) is the SAME test after removing recent realised")
    print("volatility from both sides — because volatility clusters, and a feature that only")
    print("predicts tomorrow's range by proxying for yesterday's is telling us nothing that price")
    print("does not already say for free. Only 'SURVIVES BOTH' is worth anything.")
    print("'abs_open_to_close' is the VOLATILITY test: predicting how far price moves is useful for")
    print("sizing even when direction is unpredictable, because a bought option needs movement.")
    print("Everything here is IN-SAMPLE. Clearing the floor earns a walk-forward test, not a trade.")

    _walk_forward(per_symbol, split=args.split_date, floor=floor)
    return 0


def _walk_forward(
    per_symbol: dict[str, tuple[list[dt.date], dict[str, list[float]], dict[str, list[float]]]],
    *,
    split: dt.date,
    floor: float,
) -> None:
    """The same tests, run separately on an EARLY and a LATE period.

    Everything above is in-sample: the features were chosen knowing what
    options traders watch, and 27 tests were run on one history. The only
    cheap defence against that is to ask whether a result found in the early
    period is still there in the late one, on data that had no chance to
    influence the choice.

    A real effect shows the SAME SIGN in both halves with meaningful size. An
    effect that flips sign, or vanishes, was a description of the first half
    and nothing more. This is reported for every feature rather than only the
    winners, because showing the survivors alone is how a walk-forward test
    gets quietly turned into a second in-sample search.
    """
    print(f"\n\n=== WALK-FORWARD: does it still hold after {split}? ===")
    print("Same tests, split into two periods. A real effect keeps its sign and size in both.")
    print("An effect that flips sign or collapses was a story about the first period only.\n")

    for symbol, (days, rows, targets) in per_symbol.items():
        cut = np.array([d < split for d in days])
        if cut.sum() < 100 or (~cut).sum() < 100:
            print(f"{symbol}: not enough days on both sides of {split} — skipped")
            continue
        print(f"--- {symbol} ---  early n={int(cut.sum()):,}   late n={int((~cut).sum()):,}")
        header = f"{'feature':<24}{'target':<24}{'early rho':>11}{'late rho':>10}{'late t':>9}  verdict"
        print(header)
        scored = []
        for feature_name, values in rows.items():
            x_all = np.asarray(values, dtype=float)
            for target_name, target_values in targets.items():
                y_all = np.asarray(target_values, dtype=float)
                usable = ~np.isnan(x_all) & ~np.isnan(y_all)
                early = usable & cut
                late = usable & ~cut
                if early.sum() < 100 or late.sum() < 100:
                    continue
                rho_early, t_early = _spearman_t(x_all[early], y_all[early])
                rho_late, t_late = _spearman_t(x_all[late], y_all[late])
                scored.append((feature_name, target_name, rho_early, t_early, rho_late, t_late))

        for feature_name, target_name, rho_early, t_early, rho_late, t_late in sorted(
            scored, key=lambda r: -abs(r[3])
        ):
            same_sign = rho_early * rho_late > 0
            if abs(t_early) > floor and same_sign and abs(t_late) > 1.96:
                verdict = "HOLDS OUT-OF-SAMPLE"
            elif abs(t_early) > floor and same_sign:
                verdict = "same sign, weaker"
            elif abs(t_early) > floor:
                verdict = "FLIPPED SIGN — was noise"
            else:
                verdict = ""
            print(
                f"{feature_name:<24}{target_name:<24}{rho_early:>+11.3f}{rho_late:>+10.3f}{t_late:>+9.2f}  {verdict}"
            )
        print()

    print("Only 'HOLDS OUT-OF-SAMPLE' means anything. Everything else is a description of the past.")


if __name__ == "__main__":
    raise SystemExit(main())
