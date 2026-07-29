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
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from te.data.asof import bars_asof, interval_to_timedelta
from te.data.barstore import BarStore
from te.domain.clock import assume_utc as _as_utc
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.persistence.models import CycleEvaluationRow, EvaluationConditionRow

Barrier = Literal["target", "stop", "time"]

_BREAKOUT_CONDITION_LABEL = "breakout close beyond opening range"
_CLOSE_RE = re.compile(r"close=([0-9.]+)")


@dataclass(frozen=True)
class LabeledFiring:
    """One triple-barrier-labeled ORB firing. `weight` is the uniqueness
    weight (1 / average concurrent-label count over the label's lifespan),
    filled in by `compute_uniqueness_weights` — `1.0` until then."""

    evaluation_id: str
    instrument: str
    entry_ts: dt.datetime
    exit_ts: dt.datetime
    entry_premium: Paise
    label: int
    barrier: Barrier
    weight: float = 1.0


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
) -> tuple[int, Barrier, dt.datetime]:
    """Walks forward from `entry_ts` through recorded bars (via
    `bars_asof`) to see which barrier is touched first:

    - the bar's high reaching `net_target` (`target_premium +
      round_trip_cost_per_unit`, computed via the real `CostModel`) -> label
      1, barrier "target";
    - the bar's low reaching the (gross) stop premium -> label 0, barrier
      "stop";
    - neither touched by `entry_ts + max_hold` -> label 0, barrier "time".

    Returns `(label, barrier, resolved_at)`.
    """
    stop_premium = Paise(entry_premium - stop_distance)
    gross_target_premium = Paise(entry_premium + target_distance)
    cost_per_unit = cost_model.round_trip(
        entry_premium=entry_premium, exit_premium=gross_target_premium, qty=1, exchange=exchange, on=entry_ts.date()
    ).total
    net_target_premium = Paise(gross_target_premium + cost_per_unit)

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
        if high >= net_target_premium:
            return 1, "target", bar_ts
        if low <= stop_premium:
            return 0, "stop", bar_ts

    return 0, "time", horizon_end


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
) -> list[LabeledFiring]:
    """Labels every `verdict == "traded"` evaluation for `strategy` — the
    plan's explicit instruction to include vetoed/zero-sized firings, since
    censoring on trades risk already accepted biases the model toward sizes
    already taken."""
    with session_factory() as session:
        evaluations = list(
            session.execute(
                select(CycleEvaluationRow).where(
                    CycleEvaluationRow.strategy == strategy, CycleEvaluationRow.verdict == "traded"
                )
            )
            .scalars()
            .all()
        )
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
                )
            )

    weights = compute_uniqueness_weights([(f.entry_ts, f.exit_ts) for f in firings])
    return [replace(f, weight=w) for f, w in zip(firings, weights, strict=True)]
