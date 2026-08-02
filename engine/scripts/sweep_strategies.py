#!/usr/bin/env python
"""Tests MANY entry rules against the real option-premium archive at once.

Usage:
    python -m scripts.sweep_strategies --list
    python -m scripts.sweep_strategies                      # every rule, baseline geometry
    python -m scripts.sweep_strategies --rules orb60,twap_reversion_2
    python -m scripts.sweep_strategies --geometries 20/20/180,10/10/30

### Why this exists

The barrier sweep condemned ONE rule (`orb60`) wrapped in a bought option,
and `measure_index_edge.py` then showed why: the breakout's directional
information, such as it is, takes ~3 hours to appear, while a bought option
decays every minute. That is a statement about ORB, not about option buying
in general — a rule whose move lands in 20 minutes would not pay that tax.

So the honest next question is not "is buying dead" but "does ANY rule we
can write produce a fast enough move". That question is cheap to ask now:
84 million real 1-minute option bars are on disk, so a candidate rule can be
falsified in minutes instead of waiting months of forward paper trading.

### What this is NOT

Not a search for the best in-sample number. Running 15 rules x several
geometries is ~50-100 trials, and the best of 100 pure-noise trials lands
around +2.5 sigma by construction. The output therefore prints a
multiple-comparison floor beside every result, and a rule that clears its
own t-stat but not the floor is reported as NOT SIGNIFICANT. Every rule run
here must also be added to the `TrialLedger` before any of it informs a
model — see `te/ml/trials.py`. This script measures, it does not promote.

### Point-in-time discipline

Every indicator is computed causally: rolling windows are `.shift(1)` so the
signal bar itself is never in its own reference window, VWAP is a cumulative
sum, and no rule may reference a bar later than the one it fires on. Entry
is taken at the CLOSE of the signal bar (`event_ts + 1 minute`), which is
the first moment a live engine could have acted on it. Option premiums are
read through `bars_asof`, the same point-in-time gate every other reader
uses.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import statistics
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from functools import partial

import numpy as np
import pandas as pd

from te.backtest.strategy_lab import _WholeSymbolCache
from te.data.asof import bars_asof
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.lot_size_history import load_lot_size_history, lot_size_on
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.domain.signal import Direction
from te.ml.labeling import label_one_firing_on_premium
from te.ml.metrics import expected_max_z
from te.settings import Settings
from te.strategy.indicators import prior_rolling, rsi, session_average

#: Entry is never taken after this. Mirrors `te.backtest.replay`'s
#: `DEFAULT_LAST_ENTRY` — a firing the live engine would refuse must not
#: become evidence for or against a rule it would never trade.
LAST_ENTRY = dt.time(14, 35)

#: At most this many entries per rule per day, taken in time order. The live
#: engine caps entries per underlying per day, and a rule that fires 40 times
#: in a trending session would otherwise have its statistics dominated by a
#: handful of days.
MAX_ENTRIES_PER_DAY = 3


@dataclass(frozen=True)
class Geometry:
    stop_pct: Decimal
    target_pct: Decimal
    max_hold: dt.timedelta

    @classmethod
    def parse(cls, text: str) -> Geometry:
        stop, target, minutes = text.split("/")
        return cls(Decimal(stop), Decimal(target), dt.timedelta(minutes=int(minutes)))

    def __str__(self) -> str:
        return f"{self.stop_pct:g}/{self.target_pct:g}/{int(self.max_hold.total_seconds() // 60)}m"


@dataclass(frozen=True)
class Outcome:
    barrier: str
    r_multiple: float




# ---------------------------------------------------------------------------
# The rules. Each takes a day-frame (with a causal `ist` column) and yields
# `(signal_row_index, direction)`. None may look forward.
# ---------------------------------------------------------------------------

Rule = Callable[[pd.DataFrame], Iterator[tuple[int, Direction]]]


def _opening_range_breakout(df: pd.DataFrame, *, minutes: int, fade: bool) -> Iterator[tuple[int, Direction]]:
    """Close crosses out of the first `minutes` of the session.

    `fade=True` inverts the direction — buying a PE on an upside break. This
    is the same rule, not a new one, and it is here because a rule that
    loses money is evidence about its OWN direction: if breakouts genuinely
    mean-revert, the inverse should win by roughly the amount the original
    lost, minus twice the cost. That comparison is only interpretable when
    both sides fire on identical bars, which is why it shares this function.
    """
    opening = df[df["minutes_from_open"] < minutes]
    if len(opening) < 3:
        return
    high, low = float(opening["h"].max()), float(opening["l"].min())
    after = df[df["minutes_from_open"] >= minutes]
    prev_inside = True
    for idx, row in after.iterrows():
        close = float(row["c"])
        outside = close > high or close < low
        if outside and prev_inside:
            up = close > high
            if fade:
                yield idx, ("long_put" if up else "long_call")
            else:
                yield idx, ("long_call" if up else "long_put")
        prev_inside = low <= close <= high


def _twap_reversion(df: pd.DataFrame, *, sigma: float) -> Iterator[tuple[int, Direction]]:
    """Price stretched far from the session average, betting on snap-back.

    The stretch is measured in standard deviations of the rule's OWN recent
    distance from that average rather than in points, so it means the same
    thing on a quiet day as on a violent one."""
    average = session_average(df)
    distance = (df["c"] - average) / average
    scale = distance.rolling(60).std().shift(1)
    armed = True
    for idx, (dist, sd) in enumerate(zip(distance, scale, strict=True)):
        if pd.isna(dist) or pd.isna(sd) or sd == 0:
            continue
        z = dist / sd
        if armed and z > sigma:
            yield df.index[idx], "long_put"
            armed = False
        elif armed and z < -sigma:
            yield df.index[idx], "long_call"
            armed = False
        elif abs(z) < sigma / 2:
            armed = True


def _twap_momentum(df: pd.DataFrame) -> Iterator[tuple[int, Direction]]:
    """Close crosses the session average price — the simplest trend-following
    rule there is, and a useful control: if it performs like the breakout
    rules, they are all measuring the same thing."""
    average = session_average(df)
    above = df["c"] > average
    crossed = above & ~above.shift(1, fill_value=False)
    crossed_down = ~above & above.shift(1, fill_value=False)
    for idx in df.index[crossed]:
        yield idx, "long_call"
    for idx in df.index[crossed_down]:
        yield idx, "long_put"


def _ema_cross(df: pd.DataFrame, *, fast: int, slow: int) -> Iterator[tuple[int, Direction]]:
    ema_fast = df["c"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["c"].ewm(span=slow, adjust=False).mean()
    above = ema_fast > ema_slow
    for idx in df.index[above & ~above.shift(1, fill_value=False)]:
        yield idx, "long_call"
    for idx in df.index[~above & above.shift(1, fill_value=False)]:
        yield idx, "long_put"


def _donchian(df: pd.DataFrame, *, window: int) -> Iterator[tuple[int, Direction]]:
    high = prior_rolling(df["h"], window, "max")
    low = prior_rolling(df["l"], window, "min")
    for idx, close, hi, lo in zip(df.index, df["c"], high, low, strict=True):
        if pd.isna(hi) or pd.isna(lo):
            continue
        if close > hi:
            yield idx, "long_call"
        elif close < lo:
            yield idx, "long_put"


def _rsi_reversion(df: pd.DataFrame, *, low: float, high: float) -> Iterator[tuple[int, Direction]]:
    prior_rsi = rsi(df["c"]).shift(1)
    armed = True
    for idx, value in zip(df.index, prior_rsi, strict=True):
        if pd.isna(value):
            continue
        if armed and value <= low:
            yield idx, "long_call"
            armed = False
        elif armed and value >= high:
            yield idx, "long_put"
            armed = False
        elif low < value < high:
            armed = True


def _gap(df: pd.DataFrame, *, fade: bool, min_pct: float) -> Iterator[tuple[int, Direction]]:
    """Acts on the overnight gap, once, 15 minutes into the session.

    `prev_close` is attached by the caller from the PREVIOUS day's frame —
    it is the one piece of cross-day state any rule here uses, and it is
    passed in rather than looked up so this stays a pure function of the
    frame it is given."""
    if "prev_close" not in df or pd.isna(df["prev_close"].iloc[0]):
        return
    prev_close = float(df["prev_close"].iloc[0])
    open_px = float(df["o"].iloc[0])
    gap_pct = (open_px - prev_close) / prev_close * 100
    if abs(gap_pct) < min_pct:
        return
    candidates = df.index[df["minutes_from_open"] >= 15]
    if len(candidates) == 0:
        return
    up = gap_pct > 0
    follow: Direction = "long_call" if up else "long_put"
    against: Direction = "long_put" if up else "long_call"
    yield candidates[0], (against if fade else follow)


def _range_compression(df: pd.DataFrame, *, minutes: int, max_width_pct: float) -> Iterator[tuple[int, Direction]]:
    """A breakout, but only out of an UNUSUALLY TIGHT opening range.

    The premise worth testing: a breakout's move size should scale with the
    coiling that preceded it, and a tight range is the setup most likely to
    produce a move fast enough to outrun theta. If any breakout variant
    survives, the prior says it is this one."""
    opening = df[df["minutes_from_open"] < minutes]
    if len(opening) < 3:
        return
    high, low = float(opening["h"].max()), float(opening["l"].min())
    if (high - low) / low * 100 > max_width_pct:
        return
    yield from _opening_range_breakout(df, minutes=minutes, fade=False)


def _first_bar_momentum(df: pd.DataFrame, *, minutes: int) -> Iterator[tuple[int, Direction]]:
    """Continuation of the session's first `minutes` of direction, entered
    immediately after. Deliberately the crudest rule in the set — it exists
    as a floor, because a sophisticated rule that cannot beat this one is
    not earning its complexity."""
    opening = df[df["minutes_from_open"] < minutes]
    if len(opening) < 3:
        return
    move = float(opening["c"].iloc[-1]) - float(opening["o"].iloc[0])
    if move == 0:
        return
    after = df.index[df["minutes_from_open"] >= minutes]
    if len(after) == 0:
        return
    yield after[0], ("long_call" if move > 0 else "long_put")


def _random_entry(df: pd.DataFrame, *, seed_salt: int) -> Iterator[tuple[int, Direction]]:
    """A coin flip. **The control, and the most informative row in the sweep.**

    Every real rule here loses roughly the same amount, which has two very
    different explanations: either all seventeen happen to be bad, or none of
    them has any edge and what is being measured is friction. A regression or
    a t-stat cannot separate those; a control can.

    If random entries lose the SAME amount as the strategies, then the
    strategies are contributing nothing at all and the loss is the cost of
    buying and selling an option. If the strategies lose materially LESS than
    random, they carry real (if insufficient) information, and the honest
    conclusion changes from "this approach is dead" to "the edge is real but
    smaller than the friction".

    Each minute is decided INDEPENDENTLY, from a seed seeded by (day, minute)
    alone. That matters for more than tidiness: an earlier draft sampled
    without replacement from the day's full list of candidate minutes, which
    made the draw depend on how long the session turned out to be — and the
    causality test caught it. Deciding minute by minute is truncation-
    invariant, so this control is held to exactly the same no-lookahead
    standard as every real rule it is being compared against.

    Deterministic, so a re-run reproduces exactly.
    """
    candidates = df.index[df["minutes_from_open"] >= 15]
    if len(candidates) < 10:
        return
    day_seed = int(pd.Timestamp(df["ist"].iloc[0]).strftime("%Y%m%d")) + seed_salt
    # Tuned so the expected number of entries per session matches the cap the
    # real rules are held to; otherwise the control would be measured on a
    # different trade count and the comparison would not be like for like.
    entry_probability = MAX_ENTRIES_PER_DAY / 330
    for idx in candidates:
        rng = np.random.default_rng(day_seed * 10_000 + int(df.loc[idx, "minutes_from_open"]))
        if rng.random() < entry_probability:
            yield int(idx), ("long_call" if rng.random() < 0.5 else "long_put")


RULES: dict[str, Rule] = {
    "random_entry": partial(_random_entry, seed_salt=0),
    "orb15": partial(_opening_range_breakout, minutes=15, fade=False),
    "orb30": partial(_opening_range_breakout, minutes=30, fade=False),
    "orb60": partial(_opening_range_breakout, minutes=60, fade=False),
    "orb60_fade": partial(_opening_range_breakout, minutes=60, fade=True),
    "orb15_fade": partial(_opening_range_breakout, minutes=15, fade=True),
    "twap_reversion_2": partial(_twap_reversion, sigma=2.0),
    "twap_reversion_3": partial(_twap_reversion, sigma=3.0),
    "twap_momentum": _twap_momentum,
    "ema_9_21": partial(_ema_cross, fast=9, slow=21),
    "ema_21_55": partial(_ema_cross, fast=21, slow=55),
    "donchian_20": partial(_donchian, window=20),
    "donchian_60": partial(_donchian, window=60),
    "rsi_reversion": partial(_rsi_reversion, low=30, high=70),
    "gap_go": partial(_gap, fade=False, min_pct=0.3),
    "gap_fade": partial(_gap, fade=True, min_pct=0.3),
    "tight_range_breakout": partial(_range_compression, minutes=60, max_width_pct=0.35),
    "first_hour_momentum": partial(_first_bar_momentum, minutes=60),
}


def _day_frames(store: BarStore, symbol: str) -> list[tuple[dt.date, pd.DataFrame]]:
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
    days: list[tuple[dt.date, pd.DataFrame]] = []
    prev_close: float | None = None
    for day, group in frame.groupby(frame["ist"].dt.date, sort=True):
        group = group.reset_index(drop=True)
        open_ts = group["ist"].iloc[0]
        session_open = open_ts.replace(hour=9, minute=15, second=0, microsecond=0)
        group["minutes_from_open"] = (group["ist"] - session_open).dt.total_seconds() / 60
        group["prev_close"] = prev_close
        days.append((day, group))
        prev_close = float(group["c"].iloc[-1])
    return days


def _signals_for_day(rule: Rule, day_frame: pd.DataFrame) -> list[tuple[dt.datetime, Direction]]:
    """Entry timestamps and directions, capped and time-filtered.

    Entry is the signal bar's CLOSE (`event_ts + 1 minute`) — the first
    instant a live engine could have acted. Taking the bar's open, or its
    own close price at its own open time, is the classic one-bar lookahead
    that makes every rule in a sweep like this look profitable."""
    out: list[tuple[dt.datetime, Direction]] = []
    for idx, direction in rule(day_frame):
        row = day_frame.loc[idx]
        entry_ts = row["event_ts"].to_pydatetime() + dt.timedelta(minutes=1)
        if entry_ts.astimezone(IST).time() > LAST_ENTRY:
            continue
        out.append((entry_ts, direction))
        if len(out) >= MAX_ENTRIES_PER_DAY:
            break
    return out


def _evaluate(
    *,
    store: BarStore,
    contracts: OptionContractIndex,
    cost_model: CostModel,
    lot_size_for: Callable[[dt.date], int],
    entry_ts: dt.datetime,
    index_level: Decimal,
    direction: Direction,
    geometry: Geometry,
    exchange: str,
    strikes_out_of_the_money: int,
) -> Outcome | None:
    contract = contracts.nearest(
        on=entry_ts.astimezone(IST).date(),
        index_level=index_level,
        option_type="CE" if direction == "long_call" else "PE",
        strikes_out_of_the_money=strikes_out_of_the_money,
    )
    if contract is None:
        return None
    lot_size = lot_size_for(entry_ts.date())
    label = label_one_firing_on_premium(
        store=store,
        option_symbol=contract.symbol,
        entry_ts=entry_ts,
        stop_pct=geometry.stop_pct,
        target_pct=geometry.target_pct,
        max_hold=geometry.max_hold,
        cost_model=cost_model,
        exchange=exchange,
        lot_size=lot_size,
    )
    if label is None:
        return None

    ratio = float(geometry.target_pct / geometry.stop_pct)
    if label.barrier == "target":
        return Outcome("target", ratio)
    if label.barrier == "stop":
        return Outcome("stop", -1.0)

    # A time exit's P&L is not implied by the barrier — it has to be read.
    # Scoring time exits as flat (or as losses) is the quiet way a sweep
    # misreports a rule whose moves are real but slower than the horizon.
    exit_bars = bars_asof(store, contract.symbol, label.resolved_at, dt.timedelta(minutes=10), interval="1m")
    if exit_bars.empty:
        return None
    exit_premium = Paise(int(round(float(exit_bars.iloc[-1]["c"]) * 100)))
    round_trip = cost_model.round_trip(
        entry_premium=label.entry_premium,
        exit_premium=exit_premium,
        qty=lot_size,
        exchange=exchange,
        on=entry_ts.date(),
    ).total
    cost_per_unit = float(round_trip) / lot_size
    stop_distance = float(label.entry_premium) * float(geometry.stop_pct) / 100
    net = float(exit_premium) - float(label.entry_premium) - cost_per_unit
    return Outcome("time", net / stop_distance if stop_distance else 0.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instrument", default="NIFTY")
    parser.add_argument("--exchange", default="NFO")
    parser.add_argument("--rules", default="", help="comma-separated subset; default is every rule")
    parser.add_argument("--geometries", default="20/20/180", help="stop/target/holdMinutes, comma-separated")
    parser.add_argument(
        "--strikes-otm",
        default="0",
        help=(
            "comma-separated distances from spot, counted in STRIKES. 0 is at-the-money (the "
            "expensive one, and what every earlier measurement silently used); 3 on NIFTY is ~150 "
            "points out and several times cheaper per lot."
        ),
    )
    parser.add_argument("--list", action="store_true", help="print the rule names and exit")
    args = parser.parse_args()

    if args.list:
        for name in RULES:
            print(name)
        return 0

    chosen = [r.strip() for r in args.rules.split(",") if r.strip()] or list(RULES)
    unknown = [r for r in chosen if r not in RULES]
    if unknown:
        print(f"unknown rules: {', '.join(unknown)}\navailable: {', '.join(RULES)}", file=sys.stderr)
        return 2
    geometries = [Geometry.parse(g.strip()) for g in args.geometries.split(",") if g.strip()]

    settings = Settings()
    raw_store = BarStore(settings.bar_store_path)
    store = _WholeSymbolCache(settings.bar_store_path, raw_store)
    cost_model = CostModel(load_charge_rate_table(settings.charges_path))
    lot_history = load_lot_size_history(settings.charges_path.parent / "lot_sizes.yaml")
    lot_size_for = partial(lot_size_on, lot_history, args.instrument)
    contracts = OptionContractIndex(raw_store, args.instrument)

    days = _day_frames(raw_store, args.instrument)
    if not days:
        print(f"no index bars for {args.instrument}", file=sys.stderr)
        return 2
    print(f"{args.instrument}: {len(days):,} sessions, {days[0][0]}..{days[-1][0]}")
    print(f"contracts in archive: {len(contracts.expiries):,} expiries\n")

    offsets = [int(o.strip()) for o in args.strikes_otm.split(",") if o.strip()]
    trials = len(chosen) * len(geometries) * len(offsets)
    results: list[tuple[str, str, int, int, float, float, float]] = []

    for offset in offsets:
        for geometry in geometries:
            for name in chosen:
                rule = RULES[name]
                outcomes: list[Outcome] = []
                unlabelled = 0
                for _, day_frame in days:
                    for entry_ts, direction in _signals_for_day(rule, day_frame):
                        # The strike is chosen from the index level AT
                        # DECISION TIME — the signal bar's close, never
                        # anything later.
                        prior = day_frame[day_frame["event_ts"] < pd.Timestamp(entry_ts)]
                        if prior.empty:
                            continue
                        index_level = Decimal(str(float(prior["c"].iloc[-1])))
                        outcome = _evaluate(
                            store=store,
                            contracts=contracts,
                            cost_model=cost_model,
                            lot_size_for=lot_size_for,
                            entry_ts=entry_ts,
                            index_level=index_level,
                            direction=direction,
                            geometry=geometry,
                            exchange=args.exchange,
                            strikes_out_of_the_money=offset,
                        )
                        if outcome is None:
                            unlabelled += 1
                            continue
                        outcomes.append(outcome)

                n = len(outcomes)
                label = f"{name} otm{offset}"
                if n < 30:
                    print(f"  {label:<28} {geometry!s:<14} n={n:<6,} (too few, {unlabelled:,} unlabelled)")
                    continue
                rs = [o.r_multiple for o in outcomes]
                mean_r = statistics.fmean(rs)
                stdev = statistics.stdev(rs)
                t_stat = mean_r / (stdev / math.sqrt(n)) if stdev > 0 else 0.0
                wins = sum(1 for o in outcomes if o.r_multiple > 0) / n
                results.append((name, str(geometry), offset, n, mean_r, t_stat, wins))
                print(f"  {label:<28} {geometry!s:<14} n={n:<6,} unlabelled={unlabelled:,}")

    # The floor a result must clear to be interesting AFTER accounting for
    # how many rules were tried. Expected maximum of `trials` draws from a
    # standard normal, Blom's approximation — the same correction the range
    # sweep used. Reporting a raw t-stat from a 50-trial search without this
    # is the single easiest way to manufacture an edge that is not there.
    floor = expected_max_z(trials)
    print(f"\n{'rule':<22}{'geometry':<14}{'otm':>4}{'n':>8}{'mean R':>9}{'t-stat':>9}{'win %':>8}   verdict")
    for name, geometry, offset, n, mean_r, t_stat, wins in sorted(results, key=lambda r: -r[4]):
        verdict = "SIGNIFICANT" if t_stat > floor else ("positive, not significant" if mean_r > 0 else "")
        print(
            f"{name:<22}{geometry:<14}{offset:>4}{n:>8,}{mean_r:>+9.3f}{t_stat:>+9.2f}{wins:>7.1%}   {verdict}"
        )

    print(f"\n{trials} trials run -> a rule must clear t > {floor:.2f} to beat the best-of-{trials} noise floor.")
    print("mean R is in units of the stop: +1.0 means one full stop distance earned, net of real costs.")
    print("Every number here is IN-SAMPLE over the same 2024-2026 archive. A rule that clears the")
    print("floor has earned a walk-forward test, not a deployment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
