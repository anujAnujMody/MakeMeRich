"""The scheduled label -> train -> REGISTER pipeline that makes the ML layer
actually learn, plus the `ShadowMLHook` factory that lets it watch.

### Why this module exists

Until 2026-08-05 the ML layer was self-*recording*, not self-learning. Two
things were missing and both were structural, not incidental:

1. **Nothing ever trained on a schedule.** `scripts/train_meta_model.py`
   existed but had to be run by hand, and it deliberately did not register
   its result ("promotion is a manual reviewed action"). That conflated two
   different things — see below.
2. **`model_registry` was empty, so `ShadowMLHook` could not be built at
   all**, and `te.engine.scheduler` therefore passed `ml_hook=None` into
   every cycle. The gate could not observe a single decision, which means a
   1-2 month paper run would have thrown away exactly the data the model is
   supposed to learn from.

### Registering is not promoting — the distinction this module keeps

`register_model` writes an artifact and its provenance to `model_registry`.
`MaturityGate.set_stage` moves the model's authority. This module does the
FIRST and never the second. That separation is what makes it safe to train
automatically on a failing model: at `shadow` stage `MaturityGate.influence`
returns `MLInfluence(1, False, None)` for any `p` whatsoever, so a badly
fitted model registered here cannot change a single order, size or skip
reason. Promotion stays `scripts/promote_model.py`, manual and reviewed.

So a model that FAILS its DSR/PBO gates is still registered, on purpose. Per
`honest-metrics`, the failing score is recorded alongside it (`dsr`, `pbo`,
`n_trials_at_training` are columns on the row, and `notes` states the
verdict in words) rather than the model being silently dropped — the point
of nightly training is to watch those numbers move as the sample grows, and
you cannot watch a number that was never written down.
"""

from __future__ import annotations

import pandas as pd
import structlog
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from te.data.barstore import BarStore
from te.domain.costs import CostModel
from te.domain.geometry import DegenerateGeometry, ExitGeometry
from te.ml.barriers import (
    ATM_SNAPSHOTS,
    MAX_HOLD,
    RATES_VERIFIED_FROM,
    barriers,
    geometry_barrier_pct,
    round_trip_cost_in_index_points,
)
from te.ml.dataset import build_training_set
from te.ml.featurespec import SECONDARY_V1, FeatureSpec
from te.ml.gates import MaturityGate
from te.ml.labeling import label_firings_from_evaluations
from te.ml.predict import ShadowMLHook
from te.ml.registry import get_latest_model_record, load_model_artifact, register_model
from te.ml.train import ModelBackend, TrainResult, train_meta_model
from te.ml.trials import TrialLedger

logger = structlog.get_logger(__name__)

#: Kept identical to `scripts/train_meta_model.py`'s, and to the plan's.
DSR_THRESHOLD = 0.95
PBO_THRESHOLD = 0.05

#: The registry name the scheduled job writes under and `build_shadow_hook`
#: reads back. One name, so `get_latest_model_record` returns the newest
#: nightly model rather than whichever artifact happened to be inserted last.
MODEL_NAME = "orb-secondary"

#: Below this the job declines to train rather than producing a model from a
#: sample too small to mean anything. `honest-metrics` puts the floor for a
#: displayed Sharpe/profit-factor at n=100; a classifier needs at least as
#: much, and `validate_training_set` would in any case reject far smaller.
#: Declining is logged and returns `None` — never an exception, because a
#: thin sample on a young paper run is the EXPECTED state, not a fault.
MIN_SAMPLES_TO_TRAIN = 100


def build_labeled_dataset(
    session_factory: sessionmaker[Session],
    store: BarStore,
    cost_model: CostModel,
    *,
    geometry: ExitGeometry,
    max_lots: int,
    instruments: tuple[str, ...],
    strategy: str = "orb",
    spec: FeatureSpec = SECONDARY_V1,
) -> pd.DataFrame:
    """Triple-barrier-labelled firings joined to their feature rows.

    Every feature vector is built by `build_training_set` — the SAME
    function `ShadowMLHook` calls at inference time, per
    `pit-correctness-guard`'s single-dataset-path rule. There is deliberately
    no second "compute features for training" path here to drift from it.

    `geometry`/`max_lots` are the `ExitGeometry` and
    `Settings.paper_cycle_max_lots` the engine is ACTUALLY configured with —
    supplied by the caller (`run_nightly_training_job` in
    `te.engine.scheduler`), never selected in here. `te.ml` must not import
    `te.engine`, so this module cannot replicate
    `te.engine.scheduler._exit_geometry`'s branching without the two
    selectors drifting apart; the caller that already has that logic passes
    the result in instead. See `barriers()` for why there is no honest
    default.

    Returns an empty frame when nothing could be labelled; callers decide
    what that means rather than having an exception decide for them.
    """
    rows: list[dict[str, object]] = []
    for symbol in instruments:
        try:
            stop, target = barriers(symbol, geometry=geometry, max_lots=max_lots)
        except DegenerateGeometry as exc:
            # Whole premium sits inside the rupee cap for THIS symbol at this
            # quantity — there is no reachable stop, so no honest barrier can
            # be labelled. Skipped and logged, never guessed: the same
            # "visible, not inferred from a smaller sample" rule the
            # no-ATM-snapshot case already follows below.
            logger.warning(
                "nightly training: geometry cannot express a stop, cannot label", instrument=symbol, error=str(exc)
            )
            continue
        firings = label_firings_from_evaluations(
            session_factory,
            store,
            cost_model,
            strategy=strategy,
            exchange=ATM_SNAPSHOTS[symbol].exchange,
            stop_distance=stop,
            target_distance=target,
            max_hold=MAX_HOLD,
            instrument=symbol,
            since=RATES_VERIFIED_FROM,
            cost_per_unit=round_trip_cost_in_index_points(symbol, cost_model, RATES_VERIFIED_FROM),
        )
        logger.info("nightly training: labelled firings", instrument=symbol, n=len(firings))
        for firing in firings:
            features = build_training_set(firing.entry_ts, spec, store, None, instrument=firing.instrument)
            rows.append(
                {
                    **{c: features[c] for c in spec.columns},
                    "_label": firing.label,
                    "_entry_ts": firing.entry_ts,
                    "_exit_ts": firing.exit_ts,
                    "_weight": firing.weight,
                }
            )
    return pd.DataFrame(rows)


def _geometry_stamp(instruments: tuple[str, ...], *, geometry: ExitGeometry, max_lots: int) -> str:
    """The stop/target percentages a label was computed at, per symbol —
    written into `model_registry.notes`.

    Every label in `model_registry` used to be computed at a hardcoded
    20%/20% with no record that it was 20/20, so a future geometry change
    (a different rupee cap, a different `target_risk_multiple`) could not be
    told apart from an old model in the one place its provenance is kept.
    `model_registry` has no dedicated geometry column this module is allowed
    to add (that table lives in `te.ml.registry`, outside this task's file
    list) — `notes` is the honest place available to record it loudly rather
    than leave the mismatch silent again. A symbol whose geometry is
    degenerate at this quantity is named as `unlabelled (degenerate)` rather
    than omitted, so a later reader does not mistake its absence for an
    oversight.
    """
    parts: list[str] = []
    for symbol in instruments:
        if symbol not in ATM_SNAPSHOTS:
            continue
        try:
            stop_pct, target_pct = geometry_barrier_pct(symbol, geometry=geometry, max_lots=max_lots)
        except DegenerateGeometry:
            parts.append(f"{symbol}=unlabelled (degenerate)")
            continue
        parts.append(f"{symbol}=-{float(stop_pct):.2f}%/+{float(target_pct):.2f}% of premium")
    return ", ".join(parts)


def train_and_register(
    session_factory: sessionmaker[Session],
    engine: Engine,
    store: BarStore,
    cost_model: CostModel,
    *,
    geometry: ExitGeometry,
    max_lots: int,
    instruments: tuple[str, ...],
    backend: ModelBackend = ModelBackend.XGBOOST,
    spec: FeatureSpec = SECONDARY_V1,
    run_id: str | None = None,
) -> TrainResult | None:
    """Labels, trains, and registers one model. Returns `None` — never
    raises — when there is nothing worth training on.

    `None` covers three ordinary, expected outcomes that are NOT failures:
    no labelled firings yet, fewer than `MIN_SAMPLES_TO_TRAIN`, or
    `validate_training_set` refusing the dataset (a single-class label
    column, say). All three are the normal state of a young paper run, and a
    scheduled job that raised on them would fill the log with alarms about
    the system working correctly.
    """
    frame = build_labeled_dataset(
        session_factory, store, cost_model, geometry=geometry, max_lots=max_lots, instruments=instruments, spec=spec
    )
    if len(frame) < MIN_SAMPLES_TO_TRAIN:
        logger.info(
            "nightly training: declined, sample too small",
            n=len(frame),
            required=MIN_SAMPLES_TO_TRAIN,
        )
        return None

    features = frame[list(spec.columns)]
    labels = frame["_label"].astype(int)
    ledger = TrialLedger(engine)

    try:
        result = train_meta_model(
            features=features,
            labels=labels,
            prediction_times=frame["_entry_ts"],
            evaluation_times=frame["_exit_ts"],
            spec=spec,
            trial_ledger=ledger,
            run_id=run_id or f"{MODEL_NAME}-nightly-{len(frame)}",
            sample_weights=frame["_weight"],
            backend=backend,
        )
    except ValueError as exc:
        # `validate_training_set` raises rather than warns — a dataset it
        # rejects cannot produce a trustworthy model, so declining is the
        # correct outcome. Logged at info, not error: on a young sample this
        # is the machinery working.
        logger.info("nightly training: refused by validate_training_set", reason=str(exc))
        return None

    # NaN PBO means NOT EVALUATED (fewer than 2 configs produced a usable
    # out-of-sample series) and must never read as a pass — same rule as
    # `scripts/train_meta_model.py`.
    pbo_evaluated = result.pbo.pbo == result.pbo.pbo
    dsr_ok = result.dsr > DSR_THRESHOLD
    pbo_ok = pbo_evaluated and result.pbo.pbo < PBO_THRESHOLD
    verdict = (
        "gates PASSED (promotion is still manual)"
        if dsr_ok and pbo_ok
        else f"gates NOT met: DSR {'pass' if dsr_ok else 'fail'}, "
        f"PBO {'pass' if pbo_ok else ('not evaluated' if not pbo_evaluated else 'fail')}"
    )

    previous = get_latest_model_record(session_factory, MODEL_NAME)
    register_model(
        session_factory,
        model=result.model,
        name=MODEL_NAME,
        version=(previous.version + 1) if previous is not None else 1,
        gbm_library=backend.value,
        # `None` on purpose, and it has a consequence worth stating: nothing
        # in this codebase computes a calibration slope yet. `MetaModel`
        # carries only the fitted `CalibratedClassifierCV` and its
        # `FeatureSpec`, and `TrainResult` does not expose the per-observation
        # out-of-sample probabilities a slope would have to be regressed on.
        # `scripts/promote_model.py` reads this column and fails its
        # "slope in [0.8, 1.2]" criterion when it is `None` — so promotion
        # stays blocked until someone measures it. That is the correct
        # "not yet", and far better than writing a placeholder number that
        # would let a model be promoted on an unverified criterion.
        calibration_slope=None,
        dsr=result.dsr,
        pbo=result.pbo.pbo,
        n_trials_at_training=result.n_trials_at_training,
        n_labeled_samples=result.n_labeled_samples,
        notes=(
            f"registered by the nightly job, stays at shadow stage — {verdict}; "
            f"labelled at {_geometry_stamp(instruments, geometry=geometry, max_lots=max_lots)}"
        ),
    )
    logger.info(
        "nightly training: registered",
        samples=result.n_labeled_samples,
        dsr=result.dsr,
        pbo=result.pbo.pbo,
        trials=result.n_trials_at_training,
        verdict=verdict,
    )
    return result


def build_shadow_hook(
    session_factory: sessionmaker[Session],
    store: BarStore,
    *,
    name: str = MODEL_NAME,
) -> ShadowMLHook | None:
    """The latest registered model wrapped as an `MLHook`, or `None` when
    none has been trained yet.

    `None` is the honest answer before the first nightly run, and the caller
    passes it straight through as `ml_hook=None` — which is exactly the
    behaviour that shipped before this module existed. So wiring this in
    cannot change anything until a model genuinely exists.
    """
    record = get_latest_model_record(session_factory, name)
    if record is None:
        return None
    model = load_model_artifact(session_factory, record.id)
    return ShadowMLHook(
        model=model,
        spec=model.feature_spec,
        store=store,
        gate=MaturityGate(session_factory),
        session_factory=session_factory,
    )


class ShadowHookProvider:
    """Supplies the current shadow hook to the paper cycle, rebuilding it
    only when a NEWER model has been registered.

    The paper cycle runs every minute; unpickling the model artifact that
    often would be pure waste, and holding one built at process start would
    mean last night's newly trained model is ignored until someone restarts
    the engine — which for a system whose whole point is to learn nightly is
    the worse of the two bugs. So the cheap half is done every call
    (`get_latest_model_record` deliberately excludes `artifact_blob`, see
    `te.ml.registry`) and the expensive half only on a version change.
    """

    def __init__(self, session_factory: sessionmaker[Session], store: BarStore, *, name: str = MODEL_NAME) -> None:
        self._session_factory = session_factory
        self._store = store
        self._name = name
        self._record_id: int | None = None
        self._hook: ShadowMLHook | None = None

    def __call__(self) -> ShadowMLHook | None:
        try:
            record = get_latest_model_record(self._session_factory, self._name)
        except Exception:  # noqa: BLE001 — never block a trading cycle to look up a shadow model
            logger.exception("could not read the model registry; running with no ML influence")
            return None
        if record is None:
            return None
        if record.id != self._record_id:
            try:
                model = load_model_artifact(self._session_factory, record.id)
            except Exception:  # noqa: BLE001 — a corrupt artifact must not stop trading
                logger.exception("could not load the registered model", record_id=record.id)
                return None
            self._hook = ShadowMLHook(
                model=model,
                spec=model.feature_spec,
                store=self._store,
                gate=MaturityGate(self._session_factory),
                session_factory=self._session_factory,
            )
            self._record_id = record.id
            logger.info("shadow ML hook loaded", record_id=record.id, version=record.version)
        return self._hook


def run_nightly_training(
    session_factory: sessionmaker[Session],
    engine: Engine,
    store: BarStore,
    cost_model: CostModel,
    *,
    geometry: ExitGeometry,
    max_lots: int,
    instruments: tuple[str, ...],
) -> None:
    """Scheduler entry point. Swallows nothing it can act on, but never lets
    a training failure escape into the scheduler — a job that cannot learn
    tonight must not be able to affect tomorrow's trading, and APScheduler
    would otherwise surface this as a job error on a system that is
    functioning correctly.

    `geometry`/`max_lots` are REQUIRED, on purpose, with no default: the
    caller (`run_nightly_training_job` in `te.engine.scheduler`) already
    builds the `ExitGeometry` the engine is actually configured with (via its
    own `_exit_geometry(settings)`) and must pass that same object and
    `settings.paper_cycle_max_lots` straight through — `te.ml` cannot select
    it itself without importing `te.engine` or duplicating that selector, and
    a hardcoded fallback here is the exact bug this whole change removes.
    """
    tradeable = tuple(s for s in instruments if s in ATM_SNAPSHOTS)
    skipped = tuple(s for s in instruments if s not in ATM_SNAPSHOTS)
    if skipped:
        # Never silently dropped: an instrument with no measured ATM snapshot
        # cannot have its premium barriers restated in index points, so it
        # genuinely cannot be labelled — but that must be visible, not
        # inferred from a smaller-than-expected sample.
        logger.warning("nightly training: no ATM snapshot, cannot label", instruments=skipped)
    if not tradeable:
        logger.info("nightly training: no labellable instruments configured")
        return
    try:
        train_and_register(
            session_factory, engine, store, cost_model, geometry=geometry, max_lots=max_lots, instruments=tradeable
        )
    except Exception:  # noqa: BLE001 — a learning failure must never break the engine
        logger.exception("nightly training failed", instruments=tradeable)
