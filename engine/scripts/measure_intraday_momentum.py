#!/usr/bin/env python
"""Does the MORNING move predict the LAST 30 MINUTES of the day?

Usage:
    python -m scripts.measure_intraday_momentum
    python -m scripts.measure_intraday_momentum --instrument NIFTY

### Why this exists

`measure_index_edge.py` showed the ORB breakout carries almost no directional
information until ~3 hours out, by which point a bought option has decayed —
which is why every option-buying geometry lost. That is a verdict on ORB, not
on option buying, and it leaves an obvious question unanswered: is there a
signal whose payoff arrives FAST enough that decay does not eat it?

The published literature points at one. Gao, Han, Li & Zhou (Journal of
Financial Economics, "Market Intraday Momentum") document that the first
half-hour return predicts the LAST half-hour return on the S&P 500 ETF, with
a predictive R^2 around 1.6% — large at this frequency — rising when the
second-to-last half-hour is added. It has been replicated on FTSE 100,
EuroStoxx 50 and Chinese index futures, and the effect is reported to be
stronger on high-volatility, high-volume days.

**No India-specific replication was found.** So for us this is a hypothesis
imported from other markets, and importing a result is not evidence. This
script tests it on our own NIFTY 1-minute history before any effort is spent
on the option wrapper — the same fail-cheap ordering that made
`measure_index_edge.py` worth writing before the barrier sweep.

### Why the index and not options

Deliberately no options, no costs, no contract resolution. If the underlying
does not move predictably in the last half hour, no choice of strike or
expiry creates that movement, and the expensive premium-labelled sweep can be
cancelled outright. Costs are excluded for the same reason they were in
`measure_index_edge.py`: the question here is "is there information", and
loading it with option frictions answers a different question.

### Reading the output

`t-stat` is the standard "could this be luck?" measure; |t| > 2 is the
conventional bar, and that bar is already generous given how many variants
are printed below. A hit rate near 50% with |t| < 2 is exactly what a
non-existent effect looks like, and is the honest outcome to expect.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

from te.data.barstore import BarStore
from te.domain.clock import IST
from te.settings import Settings

#: The half-hour boundaries, in IST. NSE runs 09:15-15:30, so the "first
#: half-hour" ends 09:45 and the "last half-hour" starts 15:00.
OPEN = dt.time(9, 15)
FIRST_HALF_HOUR_END = dt.time(9, 45)
SECOND_LAST_START = dt.time(14, 30)
LAST_HALF_HOUR_START = dt.time(15, 0)
CLOSE = dt.time(15, 30)

#: A session must have at least this many 1-minute bars to be used. Filters
#: out half-days and the Muhurat evening session, whose shape would otherwise
#: be pooled with normal days as if it were one.
MIN_BARS_PER_SESSION = 300


@dataclass(frozen=True)
class Session:
    day: dt.date
    #: First half-hour return measured from the PREVIOUS CLOSE, which is the
    #: paper's definition — it deliberately includes the overnight gap,
    #: because the gap is part of the overnight information shock the effect
    #: is attributed to.
    r_first_with_gap: float
    #: The same window measured from today's open instead, excluding the gap.
    #: Reported alongside so we can see whether any effect is really about
    #: the morning session or just about the gap.
    r_first_no_gap: float
    #: 14:30-15:00. The paper's second predictor.
    r_second_last: float
    #: 15:00-15:30. The thing being predicted. Known only after entry.
    r_last: float
    #: Both measured on data available BEFORE 15:00, so they can be used to
    #: condition a live decision.
    realised_vol: float
    volume: float


def _close_at_or_before(day_frame: pd.DataFrame, moment: dt.time) -> float | None:
    """Last close at or before `moment`. Never looks past it."""
    upto = day_frame[day_frame["ist"].dt.time <= moment]
    if upto.empty:
        return None
    return float(upto["c"].iloc[-1])


def _sessions(store: BarStore, symbol: str) -> list[Session]:
    frame = store.read(
        symbol=symbol,
        start=dt.datetime(2000, 1, 1, tzinfo=dt.UTC),
        end=dt.datetime(2100, 1, 1, tzinfo=dt.UTC),
        interval="1m",
    )
    if frame.empty:
        return []
    frame = frame.drop_duplicates(subset="event_ts", keep="last").sort_values("event_ts").reset_index(drop=True)
    frame["ist"] = frame["event_ts"].dt.tz_convert(IST)

    sessions: list[Session] = []
    prev_close: float | None = None
    for day, group in frame.groupby(frame["ist"].dt.date, sort=True):
        group = group.reset_index(drop=True)
        day_close = float(group["c"].iloc[-1])
        if len(group) < MIN_BARS_PER_SESSION:
            # Still carry the close forward: a short session is a real
            # session for the purpose of the NEXT day's overnight gap, even
            # though its own shape is not comparable.
            prev_close = day_close
            continue

        open_px = float(group["o"].iloc[0])
        first_end = _close_at_or_before(group, FIRST_HALF_HOUR_END)
        second_last_start = _close_at_or_before(group, SECOND_LAST_START)
        last_start = _close_at_or_before(group, LAST_HALF_HOUR_START)
        final = _close_at_or_before(group, CLOSE)

        if None in (first_end, second_last_start, last_start, final) or not open_px or prev_close is None:
            prev_close = day_close
            continue

        # Everything used to CONDITION a decision must come from bars before
        # 15:00 — the moment the trade would be entered.
        before_entry = group[group["ist"].dt.time < LAST_HALF_HOUR_START]
        returns = before_entry["c"].pct_change().dropna()

        sessions.append(
            Session(
                day=day,
                r_first_with_gap=(first_end - prev_close) / prev_close,
                r_first_no_gap=(first_end - open_px) / open_px,
                r_second_last=(last_start - second_last_start) / second_last_start,
                r_last=(final - last_start) / last_start,
                realised_vol=float(returns.std()) if len(returns) > 10 else float("nan"),
                volume=float(before_entry["v"].sum()),
            )
        )
        prev_close = day_close
    return sessions


def _ols(y: np.ndarray, columns: list[np.ndarray]) -> tuple[list[float], list[float], float]:
    """Plain OLS with an intercept. Returns (betas, t-stats, R^2).

    Hand-rolled rather than pulled from statsmodels because the engine's
    dependency list is deliberately small and this is twelve lines of
    textbook algebra with no edge cases at this sample size.
    """
    design = np.column_stack([np.ones_like(y), *columns])
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ beta
    n, k = design.shape
    rss = float(resid @ resid)
    tss = float(((y - y.mean()) ** 2).sum())
    sigma2 = rss / (n - k)
    cov = sigma2 * np.linalg.inv(design.T @ design)
    se = np.sqrt(np.diag(cov))
    t_stats = beta / se
    r_squared = 1 - rss / tss if tss else 0.0
    return list(beta), list(t_stats), r_squared


def _report_regression(name: str, y: np.ndarray, predictors: dict[str, np.ndarray]) -> None:
    betas, t_stats, r_squared = _ols(y, list(predictors.values()))
    print(f"\n  {name}   (n={len(y):,},  R-squared={r_squared * 100:.2f}%)")
    print(f"    {'term':<26}{'coefficient':>14}{'t-stat':>10}")
    print(f"    {'intercept':<26}{betas[0]:>+14.6f}{t_stats[0]:>+10.2f}")
    for label, beta, t_stat in zip(predictors, betas[1:], t_stats[1:], strict=True):
        print(f"    {label:<26}{beta:>+14.6f}{t_stat:>+10.2f}")


def _report_signed_trade(name: str, predictor: np.ndarray, target: np.ndarray) -> None:
    """The tradeable form: go long when the predictor is positive, short when
    it is negative, hold 15:00-15:30, and see what the INDEX did.

    This is the number that decides whether Step 2 happens, because a
    regression can be significant on a signal too small to survive any real
    cost. Reported in basis points so it can be compared directly against the
    ~1.5-2.5% all-in friction an option round trip carries."""
    signed = np.sign(predictor) * target
    signed = signed[np.sign(predictor) != 0]
    n = len(signed)
    if n < 30:
        print(f"    {name:<34} (too few sessions to report)")
        return
    mean_bps = signed.mean() * 10_000
    t_stat = signed.mean() / (signed.std(ddof=1) / np.sqrt(n)) if signed.std(ddof=1) > 0 else 0.0
    hit = (signed > 0).mean()
    print(f"    {name:<34}{n:>7,}{mean_bps:>+11.2f}{t_stat:>+9.2f}{hit:>9.1%}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instrument", default="NIFTY")
    args = parser.parse_args()

    settings = Settings()
    store = BarStore(settings.bar_store_path)
    sessions = _sessions(store, args.instrument)
    if len(sessions) < 100:
        print(f"{args.instrument}: only {len(sessions)} usable sessions — not enough to test", file=sys.stderr)
        return 2

    print(f"Does the morning move predict the last 30 minutes?   {args.instrument}")
    print(f"{len(sessions):,} sessions, {sessions[0].day} .. {sessions[-1].day}")
    print("Index only. No options, no costs — this asks whether the INFORMATION exists.")

    first_gap = np.array([s.r_first_with_gap for s in sessions])
    first_no_gap = np.array([s.r_first_no_gap for s in sessions])
    second_last = np.array([s.r_second_last for s in sessions])
    last = np.array([s.r_last for s in sessions])

    print("\n=== 1. Predictive regressions (the paper's own test) ===")
    _report_regression("last 30m ~ first 30m (incl. overnight gap)", last, {"first 30m (with gap)": first_gap})
    _report_regression("last 30m ~ first 30m (excl. overnight gap)", last, {"first 30m (no gap)": first_no_gap})
    _report_regression(
        "last 30m ~ first 30m + 14:30-15:00",
        last,
        {"first 30m (with gap)": first_gap, "14:30-15:00": second_last},
    )

    print("\n=== 2. The tradeable version: enter 15:00 in the predicted direction, exit 15:30 ===")
    print(f"    {'predictor':<34}{'n':>7}{'mean bps':>11}{'t-stat':>9}{'hit rate':>9}")
    _report_signed_trade("first 30m (with gap)", first_gap, last)
    _report_signed_trade("first 30m (no gap)", first_no_gap, last)
    _report_signed_trade("14:30-15:00 only", second_last, last)
    _report_signed_trade("first 30m + 14:30-15:00 combined", first_gap + second_last, last)

    print("\n=== 3. Where the literature says the effect should be STRONGER ===")
    print("    (if an effect exists but does NOT concentrate here, that is a warning sign)")
    print(f"    {'subset':<34}{'n':>7}{'mean bps':>11}{'t-stat':>9}{'hit rate':>9}")
    vol = np.array([s.realised_vol for s in sessions])
    volume = np.array([s.volume for s in sessions])
    splits: list[tuple[str, np.ndarray]] = [("volatility", vol)]
    # NSE publishes no traded volume on the INDEX itself, so these bars carry
    # v=0. Splitting on a constant silently produces one empty half and one
    # full one, which reads like a real subgroup result and is not. Say so
    # instead of printing it.
    if np.nanmax(volume) > 0:
        splits.append(("volume", volume))
    else:
        print("    volume split skipped: index bars carry no traded volume (all zero)")
    for label, series in splits:
        usable = ~np.isnan(series)
        median = np.nanmedian(series)
        for half, mask in (("high", series > median), ("low", series <= median)):
            keep = mask & usable
            _report_signed_trade(f"{half} {label} days", first_gap[keep], last[keep])

    # ---------------------------------------------------------------------
    # Section 4 exists because sections 1-3 came back NEGATIVE, not merely
    # absent: every predictive coefficient has the wrong sign for momentum,
    # and the 14:30-15:00 term reached t=-2.59. That is a mean-REVERSION
    # hypothesis, and it is the opposite of what was being tested.
    #
    # It is followed up here rather than quietly reported because a
    # magnitude-weighted regression and a sign-only trade answer different
    # questions: the regression says large late moves reverse, while a
    # sign-only trade weights a 5bps drift the same as a 100bps slide. If the
    # effect is real it should concentrate in the LARGE moves, which is
    # exactly what section 4 tests. If it does not concentrate there, the
    # regression t-stat was being carried by a handful of outliers.
    # ---------------------------------------------------------------------
    print("\n=== 4. Follow-up: the signs above say REVERSION, not momentum ===")
    print("    Fading the 14:30-15:00 move instead of following it, split by how big that move was.")
    print(f"    {'subset':<34}{'n':>7}{'mean bps':>11}{'t-stat':>9}{'hit rate':>9}")
    _report_signed_trade("fade 14:30-15:00 (all days)", -second_last, last)
    magnitude = np.abs(second_last)
    thirds = (("smallest third", 0.0, 1 / 3), ("middle third", 1 / 3, 2 / 3), ("largest third", 2 / 3, 1.0))
    for label, lo, hi in thirds:
        low_cut, high_cut = np.quantile(magnitude, lo), np.quantile(magnitude, hi)
        keep = (magnitude >= low_cut) & (magnitude <= high_cut)
        _report_signed_trade(f"fade, {label} of moves", -second_last[keep], last[keep])

    print("\nHow to read this:")
    print("  A |t-stat| above 2 means the result is unlikely to be luck. Below 2, treat it as nothing.")
    print("  'mean bps' is how far the index moved our way, in hundredths of a percent.")
    print("  For context: an option round trip costs roughly 150-250 bps of the premium paid, but the")
    print("  index only has to move a fraction of that because the option amplifies it. A NEGATIVE or")
    print("  near-zero mean here kills the idea outright, whatever the regression says.")
    print("  Everything above is in-sample over one 2024-2026 history and is a reason to test further,")
    print("  never a reason to trade.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
