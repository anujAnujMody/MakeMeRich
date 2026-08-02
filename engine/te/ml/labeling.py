"""Triple-barrier labeling from ORB firings — the ONLY label source for the
secondary model.

Labels come from every `cycle_evaluations` row with `verdict == "traded"`
(`te/persistence/models.py::CycleEvaluationRow`), including firings that a
downstream stage vetoed or sized to zero lots (recorded separately in
`skipped_signals` per `te.engine.cycle`, but the *evaluation itself* still
has `verdict == "traded"` — see `te.engine.cycle.run_entry_cycle`, which
always calls `record_evaluation()` before any risk/sizing check runs).
Censoring on trades risk already accepted would bias the model toward sizes
already taken, per the plan — so every firing gets a **hypothetical entry
price**, reconstructed here from the `"breakout close beyond opening range"`
`ConditionResult.actual` string ORB itself recorded (e.g.
`"close=36.00, range=[28.00, 32.00]"`), NOT re-derived independently from
bars — this ties the label to exactly what ORB observed at decision time.

**Net, not gross**: `net_target = target_premium + round_trip_cost_per_unit`,
using the real `te.domain.costs.CostModel` (never an estimate) — so the model
never learns to predict a gross win that costs would erase. The stop barrier
is left at the configured (not cost-adjusted) level: the plan's formula is
stated only for the target side, so only the target barrier is shifted here;
see `test_label_uses_net_target_not_gross`.

Every bar this module walks through goes via `te.data.asof.bars_asof`.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from te.data.asof import bars_asof, interval_to_timedelta
from te.data.barstore import BarStore
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.clock import assume_utc as _as_utc
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.domain.signal import Direction
from te.persistence.models import CycleEvaluationRow, EvaluationConditionRow

Barrier = Literal["target", "stop", "time"]

_BREAKOUT_CONDITION_LABEL = "breakout close beyond opening range"
_CLOSE_RE = re.compile(r"close=([0-9.]+)")
_BREAKOUT_RE = re.compile(r"close=([0-9.]+), range=\[([0-9.]+), ([0-9.]+)\]")


#: Which price path a label was actually walked on. Carried on every
#: `LabeledFiring` because the two are NOT interchangeable: `index_approx`
#: labels come from `te.ml.barriers`' single-day delta snapshot with no theta
#: and no IV dynamics, so they are optimistically biased, while `real_option`
#: labels come from the contract's own printed premium. Pooling them
#: unlabelled would average a biased estimator with an unbiased one and
#: report the result as one number.
LabelSource = Literal["real_option", "index_approx"]


@dataclass(frozen=True)
class LabeledFiring:
    """One triple-barrier-labeled ORB firing. `weight` is the uniqueness
    weight (1 / average concurrent-label count over the label's lifespan),
    filled in by `compute_uniqueness_weights` — `1.0` until then.

    `entry_premium`'s UNIT depends on `source`: an option premium in paise
    for `real_option`, an index LEVEL x100 for `index_approx`. That is a
    pre-existing wart of the index path, made explicit here rather than left
    for a reader to infer."""

    evaluation_id: str
    instrument: str
    entry_ts: dt.datetime
    exit_ts: dt.datetime
    entry_premium: Paise
    label: int
    barrier: Barrier
    weight: float = 1.0
    source: LabelSource = "index_approx"
    option_symbol: str | None = None


def extract_hypothetical_entry_premium(condition_actual: str) -> Paise:
    """Parses `"close=36.00, range=[...]"` (as recorded by
    `te.strategy.orb.OrbStrategy`'s breakout condition) into the `Paise`
    entry premium ORB would have paid, whether or not risk/sizing later
    accepted the trade."""
    match = _CLOSE_RE.search(condition_actual)
    if match is None:
        raise ValueError(f"could not extract a close price from condition actual: {condition_actual!r}")
    rupees = Decimal(match.group(1))
    return Paise(int((rupees * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)))


def parse_breakout(actual: str) -> tuple[Decimal, Decimal, Decimal] | None:
    """`(close, range_low, range_high)` from the breakout condition string
    ORB records, e.g. `"close=23576.70, range=[23587.75, 23733.70]"`.

    THE single parser for that format. `te.strategy.orb` writes it and
    several readers consume it (direction, entry premium, range width), so
    every extra copy of this regex is another place to update when the
    string changes — and the failure mode is silent, since an unmatched
    string degrades to `None` and the firing is quietly dropped. Public
    precisely so callers outside this module import it instead of
    re-deriving it.
    """
    match = _BREAKOUT_RE.match(actual)
    if match is None:
        return None
    close, low, high = (Decimal(g) for g in match.groups())
    return close, low, high


def direction_from_breakout(actual: str) -> Direction | None:
    """Recovers the firing's DIRECTION from what ORB itself recorded.

    `CycleEvaluationRow` carries no direction column, but the condition
    string determines it unambiguously: a close above the range high is the
    `long_call` breakout, a close below the range low is the `long_put`.
    Derived from what the rule observed rather than re-computed from bars,
    for the same reason the entry premium is.
    """
    parsed = parse_breakout(actual)
    if parsed is None:
        return None
    close, low, high = parsed
    if close > high:
        return "long_call"
    if close < low:
        return "long_put"
    return None


def label_one_firing(
    *,
    store: BarStore,
    instrument: str,
    entry_ts: dt.datetime,
    entry_premium: Paise,
    stop_distance: Paise,
    target_distance: Paise,
    max_hold: dt.timedelta,
    cost_model: CostModel,
    exchange: str,
    interval: str = "1m",
    direction: Direction = "long_call",
    cost_per_unit: Paise | None = None,
) -> tuple[int, Barrier, dt.datetime]:
    """Walks forward from `entry_ts` through recorded bars (via `bars_asof`)
    to see which barrier is touched first, in the direction actually traded:

    - the target reached -> label 1, barrier "target";
    - the (gross) stop reached -> label 0, barrier "stop";
    - neither touched by `entry_ts + max_hold` -> label 0, barrier "time".

    **`direction` is load-bearing.** ORB fires `long_put` on a DOWNSIDE
    breakout, and that trade wins when the underlying FALLS. This function
    used to assume every firing was long the underlying — checking the bar
    HIGH for the target and the LOW for the stop unconditionally — which
    inverted the label of every downside firing. Measured on the 6,172
    replayed firings: 3,078 of them (49.9%) are `long_put`, so essentially
    half the training set was labelled backwards, and the apparent win rate
    came out at 8.7% against a ~33% random-walk baseline for these 1:2
    barriers.

    **`cost_per_unit` must be supplied when the barriers are not in the
    traded instrument's own price unit.** The internal `CostModel` call
    prices a round trip on `entry_premium`, which is only meaningful when
    that IS the option premium (the live path). In a replay the barriers are
    INDEX distances and `entry_premium` is an index LEVEL, so pricing costs
    on ~24,400 as though it were a premium returned Rs 105.18 per unit and
    inflated a 73.9-point target to 179.1 points — 2.42x — while leaving the
    stop untouched. That is the same index-level-as-premium confusion that
    once stopped the engine trading at all; here it silently poisons labels
    instead. Pass the cost already converted into the barrier's unit.

    Returns `(label, barrier, resolved_at)`.
    """
    if cost_per_unit is None:
        gross_for_costing = Paise(entry_premium + target_distance)
        cost_per_unit = cost_model.round_trip(
            entry_premium=entry_premium,
            exit_premium=gross_for_costing,
            qty=1,
            exchange=exchange,
            on=entry_ts.date(),
        ).total

    # Costs always make the TARGET harder to reach, never the stop easier —
    # the plan states the net adjustment for the target side only.
    if direction == "long_call":
        target_level = Paise(entry_premium + target_distance + cost_per_unit)
        stop_level = Paise(entry_premium - stop_distance)
    else:
        target_level = Paise(entry_premium - target_distance - cost_per_unit)
        stop_level = Paise(entry_premium + stop_distance)

    horizon_end = entry_ts + max_hold
    bar_span = interval_to_timedelta(interval)
    bars = bars_asof(store, instrument, horizon_end, max_hold + bar_span, interval=interval)
    path = bars[bars["event_ts"] > entry_ts]

    for _, bar in path.iterrows():
        bar_ts: dt.datetime = bar["event_ts"].to_pydatetime()
        if bar_ts > horizon_end:
            break
        high = Paise(int(round(float(bar["h"]) * 100)))
        low = Paise(int(round(float(bar["l"]) * 100)))
        if direction == "long_call":
            target_hit = high >= target_level
            stop_hit = low <= stop_level
        else:
            target_hit = low <= target_level
            stop_hit = high >= stop_level
        # STOP WINS A TIE. A 1-minute bar records only its high and low, not
        # the order they occurred in, so when both barriers sit inside one
        # bar the true outcome is genuinely unknown. Checking the target
        # first — as this did until 2026-08-01 — resolves every one of those
        # ambiguous bars as a WIN, which biases every label optimistically
        # in a direction no downstream metric can reveal.
        #
        # Resolving them as losses is the conservative reading and matches
        # what a live order would most often suffer: the stop is a resting
        # order that fills on the first touch, while the target needs the
        # move to hold. NautilusTrader exposes this same ambiguity as an
        # explicit `bar_adaptive_high_low_ordering` setting rather than
        # silently picking the favourable side.
        if stop_hit:
            return 0, "stop", bar_ts
        if target_hit:
            return 1, "target", bar_ts

    return 0, "time", horizon_end


@dataclass(frozen=True)
class PremiumLabel:
    """A label walked on a REAL option contract's own 1-minute premium path.

    `entry_premium` is the contract's premium in paise — genuinely money,
    unlike the index-path label's `entry_premium`, which is an index LEVEL
    x100 wearing the same type. That is exactly why `LabeledFiring.source`
    exists: the two are not comparable and must never be pooled unlabelled.
    """

    label: int
    barrier: Barrier
    resolved_at: dt.datetime
    entry_premium: Paise
    option_symbol: str


def premium_barrier_levels(
    *,
    entry_premium: Paise,
    stop_pct: Decimal,
    target_pct: Decimal,
    cost_model: CostModel,
    exchange: str,
    lot_size: int,
    on: dt.date,
) -> tuple[Paise, Paise]:
    """`(stop_level, target_level)` — the two premium levels a firing is
    walked against.

    Shared with `te.backtest.strategy_lab`, which needs the SAME levels to
    turn a barrier outcome back into a P&L. It previously re-derived them by
    hand and got the stop wrong: it scored every stop as exactly -1.0 R,
    which silently omits the round-trip cost that a stopped-out trade still
    pays. Measured on the real cost table, a stop truly costs -1.03 to
    -1.06 R depending on the premium, so every losing trade was understated
    by 3-6% of an R — a bias that flatters every strategy in the library.

    Costs make the TARGET harder to reach and leave the STOP LEVEL alone —
    the same asymmetry `label_one_firing` applies, for the same reason. That
    is about where the barriers sit, NOT about who pays the costs: both
    outcomes pay them, which is exactly what the caller must apply.
    """
    stop_distance = Paise(int((Decimal(entry_premium) * stop_pct / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)))
    target_distance = Paise(
        int((Decimal(entry_premium) * target_pct / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    )
    # The real round trip on the real lot — no delta conversion, because the
    # barriers and the premium are already in the same unit. Divided back to
    # a per-unit figure since the barriers are per-unit.
    round_trip = cost_model.round_trip(
        entry_premium=entry_premium,
        exit_premium=Paise(entry_premium + target_distance),
        qty=lot_size,
        exchange=exchange,
        on=on,
    ).total
    cost_per_unit = Paise(int((Decimal(round_trip) / lot_size).quantize(Decimal(1), rounding=ROUND_HALF_UP)))
    return Paise(entry_premium - stop_distance), Paise(entry_premium + target_distance + cost_per_unit)


def label_one_firing_on_premium(
    *,
    store: BarStore,
    option_symbol: str,
    entry_ts: dt.datetime,
    stop_pct: Decimal,
    target_pct: Decimal,
    max_hold: dt.timedelta,
    cost_model: CostModel,
    exchange: str,
    lot_size: int,
    interval: str = "1m",
    max_staleness: dt.timedelta = dt.timedelta(minutes=5),
) -> PremiumLabel | None:
    """Triple-barrier label walked on the option's OWN premium path.

    This is the honest version of `label_one_firing`. That function walks the
    INDEX and converts through a single day's delta snapshot (see
    `te.ml.barriers`) — no theta, no IV dynamics — which biases every barrier
    outcome optimistically in a direction no downstream metric can reveal.
    Here the barriers are percentages of the premium actually paid, walked
    over the premium actually printed, so decay and vol crush resolve
    barriers by themselves.

    **Both directions are LONG the premium.** ORB's `long_put` buys a PE; the
    PE's premium RISES when the index falls. The direction inversion
    `label_one_firing` must apply on the index path would label every put
    firing backwards here, so there is deliberately no `direction` parameter.

    Returns `None` — never a guess — when the contract has no usable bar at
    `entry_ts`. Three ways that happens, each a real property of the archive:
    no bars at all for the contract; a last print older than `max_staleness`
    (illiquid far strikes go minutes between trades, and pricing an entry off
    an hour-old quote invents a fill nobody could have got); or a premium of
    zero, where percentage barriers fall below one paise. Every `None` is a
    fallback the caller must COUNT, not swallow.
    """
    bar_span = interval_to_timedelta(interval)
    visible = bars_asof(store, option_symbol, entry_ts, max_staleness + bar_span, interval=interval)
    if visible.empty:
        return None
    entry_bar = visible.iloc[-1]
    if entry_bar["event_ts"].to_pydatetime() + bar_span < entry_ts - max_staleness:
        return None

    entry_premium = Paise(int(round(float(entry_bar["c"]) * 100)))
    if entry_premium <= 0:
        return None

    stop_level, target_level = premium_barrier_levels(
        entry_premium=entry_premium,
        stop_pct=stop_pct,
        target_pct=target_pct,
        cost_model=cost_model,
        exchange=exchange,
        lot_size=lot_size,
        on=entry_ts.date(),
    )

    horizon_end = entry_ts + max_hold
    bars = bars_asof(store, option_symbol, horizon_end, max_hold + bar_span, interval=interval)
    for _, bar in bars[bars["event_ts"] > entry_ts].iterrows():
        bar_ts: dt.datetime = bar["event_ts"].to_pydatetime()
        if bar_ts > horizon_end:
            break
        # Stop wins a tie — see the identical guard in `label_one_firing`.
        # Both directions are LONG the premium here, so there is no
        # direction inversion: the stop is always the low, the target always
        # the high.
        if Paise(int(round(float(bar["l"]) * 100))) <= stop_level:
            return PremiumLabel(0, "stop", bar_ts, entry_premium, option_symbol)
        if Paise(int(round(float(bar["h"]) * 100))) >= target_level:
            return PremiumLabel(1, "target", bar_ts, entry_premium, option_symbol)

    return PremiumLabel(0, "time", horizon_end, entry_premium, option_symbol)


def _try_real_premium_label(
    config: RealPremiumConfig,
    *,
    store: BarStore,
    condition_actual: str,
    direction: Direction,
    entry_ts: dt.datetime,
    max_hold: dt.timedelta,
    cost_model: CostModel,
    exchange: str,
    interval: str,
) -> PremiumLabel | None:
    """Resolves the contract this firing would have traded and labels it on
    that contract's own premiums, or `None` if the archive cannot support it.

    The strike is chosen from the INDEX LEVEL ORB itself recorded at decision
    time — never from anything later in the path — and the expiry from what
    the store actually holds rather than from `next_weekly_expiry()`: NIFTY's
    weekly expiry weekday changed twice across the archive's 2024-2026 span,
    so today's calendar resolves an older firing to a contract that never
    existed.
    """
    parsed = parse_breakout(condition_actual)
    if parsed is None:
        return None
    index_level, _, _ = parsed
    contract = config.contracts.nearest(
        on=entry_ts.astimezone(IST).date(),
        index_level=index_level,
        option_type="CE" if direction == "long_call" else "PE",
        max_days_to_expiry=config.max_days_to_expiry,
    )
    if contract is None:
        return None
    return label_one_firing_on_premium(
        store=store,
        option_symbol=contract.symbol,
        entry_ts=entry_ts,
        stop_pct=config.stop_pct,
        target_pct=config.target_pct,
        max_hold=max_hold,
        cost_model=cost_model,
        exchange=exchange,
        lot_size=config.lot_size_for(entry_ts.date()),
        interval=interval,
        max_staleness=config.max_staleness,
    )


def compute_uniqueness_weights(spans: list[tuple[dt.datetime, dt.datetime]]) -> list[float]:
    """1 / average concurrent-label count over each label's own
    `[entry_ts, exit_ts]` lifespan (Lopez de Prado's uniqueness weighting) —
    feeds both sample weights and `te.ml.cv`'s purge/embargo logic. `O(n^2)`,
    fine at the sample sizes this phase operates at (~hundreds of firings)."""
    if not spans:
        return []
    boundaries = sorted({t for span in spans for t in span})
    segments: list[tuple[dt.datetime, dt.datetime, int]] = []
    for t0, t1 in zip(boundaries, boundaries[1:], strict=False):
        if t1 <= t0:
            continue
        mid = t0 + (t1 - t0) / 2
        count = sum(1 for s0, s1 in spans if s0 <= mid <= s1)
        segments.append((t0, t1, max(count, 1)))

    weights: list[float] = []
    for entry_ts, exit_ts in spans:
        total = (exit_ts - entry_ts).total_seconds()
        if total <= 0:
            weights.append(1.0)
            continue
        weighted_concurrency = 0.0
        for t0, t1, count in segments:
            lo, hi = max(t0, entry_ts), min(t1, exit_ts)
            if hi > lo:
                weighted_concurrency += count * (hi - lo).total_seconds()
        avg_concurrency = weighted_concurrency / total if total > 0 else 1.0
        weights.append(1.0 / avg_concurrency if avg_concurrency > 0 else 1.0)
    return weights


@dataclass(frozen=True)
class RealPremiumConfig:
    """Everything `label_firings_from_evaluations` needs to attempt the real
    premium path. Grouped rather than passed as four coupled optionals, so
    "use real premiums" is one decision that cannot be half-made — a
    `contracts` index without a `lot_size` would price costs on the wrong
    notional and adjust every target barrier by the wrong amount.

    `stop_pct`/`target_pct` are percentages OF THE PREMIUM, which is how the
    barriers were defined before `te.ml.barriers` had to restate them as
    index points. On this path they need no restating.
    """

    contracts: OptionContractIndex
    stop_pct: Decimal
    target_pct: Decimal
    #: `(trade date) -> lot size`, NOT a fixed int.
    #:
    #: NIFTY's lot size changed twice inside the option archive's span — 25
    #: to 75 on 2024-11-20 when SEBI raised the minimum contract value to
    #: Rs 15 lakh, and 75 to 65 from the January 2026 series. Lot size sets
    #: how far the Rs 20-per-order brokerage is spread, so pricing the whole
    #: span at one size shifts cost-per-unit by more than 2x at the extremes
    #: — which moves the cost-adjusted target and therefore the win rate,
    #: invisibly.
    #:
    #: A callable rather than a table so this module stays free of config
    #: loading; `te.data.lot_size_history` supplies the real one.
    lot_size_for: Callable[[dt.date], int]
    max_staleness: dt.timedelta = dt.timedelta(minutes=5)
    #: Weekly default. BANKNIFTY/BANKEX are monthly-only (SEBI, Nov 2024) and
    #: need ~31 — see `OptionContractIndex.nearest` for why a too-generous
    #: value silently substitutes the wrong contract across a coverage gap.
    max_days_to_expiry: int = 7


def label_firings_from_evaluations(
    session_factory: sessionmaker[Session],
    store: BarStore,
    cost_model: CostModel,
    *,
    strategy: str,
    exchange: str,
    stop_distance: Paise,
    target_distance: Paise,
    max_hold: dt.timedelta,
    interval: str = "1m",
    instrument: str | None = None,
    since: dt.date | None = None,
    cost_per_unit: Paise | None = None,
    real_premiums: RealPremiumConfig | None = None,
) -> list[LabeledFiring]:
    """Labels every `verdict == "traded"` evaluation for `strategy` — the
    plan's explicit instruction to include vetoed/zero-sized firings, since
    censoring on trades risk already accepted biases the model toward sizes
    already taken.

    `instrument` restricts the run to one underlying. Barriers here are
    absolute distances on the INDEX, and the indices are on wildly different
    point scales — NIFTY trades near 24,400 while SENSEX trades near 78,000,
    so a single `stop_distance` that is a 0.15% move on one is a 0.05% move
    on the other. Labelling a mixed set with one barrier would silently
    assign the four underlyings four different risk/reward geometries and
    call them one dataset. Callers therefore label one instrument at a time
    with that instrument's own barriers; `None` keeps the historic
    label-everything behaviour for existing call sites.

    `real_premiums`, when supplied, makes each firing PREFER the real option
    contract's own premium path; a firing the archive does not cover falls
    back to the index approximation and is marked `source="index_approx"`.
    The fallback is deliberately not a drop: keeping only the firings the
    archive happens to cover would bias the sample toward those dates.
    Callers must count the two sources and refuse to pool them silently.
    """
    with session_factory() as session:
        where = [CycleEvaluationRow.strategy == strategy, CycleEvaluationRow.verdict == "traded"]
        if instrument is not None:
            where.append(CycleEvaluationRow.instrument == instrument)
        if since is not None:
            # `CostModel` refuses to price a trade that predates its rate
            # row's `effective_from`, deliberately: STT rose 0.10% -> 0.15%
            # on 2026-04-01, so labelling an older firing with today's rates
            # would cost-adjust its target barrier with a rate that was not
            # in force. `since` restricts a run to the era whose rates the
            # caller actually holds, rather than silently mispricing.
            where.append(CycleEvaluationRow.ts >= dt.datetime.combine(since, dt.time.min, tzinfo=dt.UTC))
        evaluations = list(session.execute(select(CycleEvaluationRow).where(*where)).scalars().all())
        # ONE query for every evaluation's breakout condition, grouped in
        # memory — a per-evaluation SELECT here is quadratic over the whole
        # firing history, which is exactly the table this function is meant
        # to grow without bound.
        conditions_by_evaluation = {
            row.evaluation_id: row
            for row in session.execute(
                select(EvaluationConditionRow).where(
                    EvaluationConditionRow.evaluation_id.in_([e.evaluation_id for e in evaluations]),
                    EvaluationConditionRow.label == _BREAKOUT_CONDITION_LABEL,
                )
            )
            .scalars()
            .all()
        }

        firings: list[LabeledFiring] = []
        for evaluation in evaluations:
            condition = conditions_by_evaluation.get(evaluation.evaluation_id)
            if condition is None or not condition.passed:
                continue

            entry_premium = extract_hypothetical_entry_premium(condition.actual)
            entry_ts = _as_utc(evaluation.ts)
            # A firing whose direction cannot be recovered is DROPPED, never
            # defaulted to long: a wrong direction inverts the label, which
            # is worse than one fewer sample.
            firing_direction = direction_from_breakout(condition.actual)
            if firing_direction is None:
                continue

            premium_label = (
                None
                if real_premiums is None
                else _try_real_premium_label(
                    real_premiums,
                    store=store,
                    condition_actual=condition.actual,
                    direction=firing_direction,
                    entry_ts=entry_ts,
                    max_hold=max_hold,
                    cost_model=cost_model,
                    exchange=exchange,
                    interval=interval,
                )
            )
            if premium_label is not None:
                firings.append(
                    LabeledFiring(
                        evaluation_id=evaluation.evaluation_id,
                        instrument=evaluation.instrument,
                        entry_ts=entry_ts,
                        exit_ts=premium_label.resolved_at,
                        entry_premium=premium_label.entry_premium,
                        label=premium_label.label,
                        barrier=premium_label.barrier,
                        source="real_option",
                        option_symbol=premium_label.option_symbol,
                    )
                )
                continue

            label, barrier, resolved_at = label_one_firing(
                store=store,
                instrument=evaluation.instrument,
                entry_ts=entry_ts,
                entry_premium=entry_premium,
                stop_distance=stop_distance,
                target_distance=target_distance,
                max_hold=max_hold,
                cost_model=cost_model,
                exchange=exchange,
                interval=interval,
                direction=firing_direction,
                cost_per_unit=cost_per_unit,
            )
            firings.append(
                LabeledFiring(
                    evaluation_id=evaluation.evaluation_id,
                    instrument=evaluation.instrument,
                    entry_ts=entry_ts,
                    exit_ts=resolved_at,
                    entry_premium=entry_premium,
                    label=label,
                    barrier=barrier,
                    source="index_approx",
                )
            )

    weights = compute_uniqueness_weights([(f.entry_ts, f.exit_ts) for f in firings])
    return [replace(f, weight=w) for f, w in zip(firings, weights, strict=True)]
