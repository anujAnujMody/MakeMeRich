"""Nested cross-validation for the secondary meta-labeling model — the
non-negotiable ordering per the plan:

- **OUTER** loop = walk-forward on time (expanding window), and PURGED —
  see `outer_walk_forward_splits()`. Metrics (DSR, PBO) are computed and
  reported ONLY from the outer loop's held-out test blocks — never from
  anything the inner loop touched.
- **INNER** loop = `te.ml.cv.PurgedKFold` (1% embargo default) over each
  outer fold's training block ONLY. Hyperparameter tuning happens ONLY
  here. EVERY inner trial (one hyperparameter configuration evaluated on
  one inner split) is recorded via `TrialLedger.record()` — this is what
  keeps the trial count N honest for `deflated_sharpe_ratio()`.

**One Sharpe convention, everywhere.** Every number that reaches
`te.ml.metrics` — `sr_hat`, and the ledger's `mean_sharpe`/`var_sharpe` —
is a PER-OBSERVATION `mean / std(ddof=1)` (`_per_observation_sharpe`),
never annualized and never sample-size-scaled, matching
`te.backtest.report.sharpe()` and what
`probabilistic_sharpe_ratio()`/`expected_max_sharpe_under_null()` document
they require. AUC appears in this module for ONE purpose only — ranking
hyperparameter configs inside the inner loop — and is never written to the
ledger's `sharpe` column.

**Model choice: three backends behind one switch.** `ModelBackend` selects
XGBoost (the default, and what every existing caller keeps getting),
CatBoost or a scikit-learn Random Forest. The nested-CV machinery around
them is identical, so all three are compared on identical data and identical
splits — the only honest way to compare them at all.

- **XGBoost** stays the default: it is the wired, installed incumbent
  (`xgboost>=3.3,<4`) and every recorded number to date came from it.
- **CatBoost** is here because its ORDERED boosting is the most
  overfit-resistant of the three below ~5,000 samples, which is squarely our
  regime (~1,200 labelled firings). Its pip install does drag in ~120MB of
  visualisation dependencies this headless engine never uses; that cost is
  accepted for the small-n robustness, not for any plotting.
- **Random Forest** is the baseline Lopez de Prado's own meta-labeling work
  used — a sanity comparator, not a candidate for sophistication.

`lightgbm` is never imported anywhere in this module — explicitly banned by
the plan (leaf-wise growth overfits at a few thousand rows) and not
installed. TabPFN is likewise excluded: its licence forbids commercial and
production use including internal commercial decision-making, which trading
real money is.

**Three models is three times the search, and the ledger must say so.**
Every backend's every inner-CV trial is recorded under the SAME
`f"{run_id}:inner"` scope, so `deflated_sharpe_ratio()` deflates against the
true total across backends rather than each model's own third of it.
Reporting a winner's DSR deflated for one backend's trials after searching
three is the exact way an edge gets fabricated. The recorded `config_hash`
includes the backend, so an identical config dict under two libraries counts
as the two distinct things it is.

**Simplification, flagged clearly:** turning classifier outputs into a
Sharpe-ratio-shaped return series for DSR/PBO requires an economic P&L
model. This phase does not yet have enough labeled shadow-mode history to
build one honestly, so — to keep the DSR/PBO *machinery* exercised
end-to-end without fabricating an economic backtest — each outer-fold
test-block sample's "return" is `+1` if the calibrated model's above/below-
0.5 call agrees with the actual triple-barrier label and `-1` otherwise (a
classification-accuracy-shaped proxy, not a claim about real premium P&L).
Refining this into the real net-of-cost trade P&L is deferred to when there
is enough shadow history to do it honestly — not blocking this phase, per
the plan's "prove the machinery, not the edge" framing (used verbatim for
Phase 5's own exit criterion).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from scipy.stats import kurtosis, skew
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score
from xgboost import XGBClassifier

from te.ml.calibrate import fit_platt_calibrator
from te.ml.cv import PurgedKFold, purge_and_embargo
from te.ml.featurespec import FeatureSpec
from te.ml.metrics import PboResult, deflated_sharpe_ratio, probability_of_backtest_overfitting
from te.ml.model import MetaModel
from te.ml.trials import TrialLedger

FloatArray = np.ndarray[tuple[int, ...], np.dtype[np.float64]]
IntArray = np.ndarray[tuple[int, ...], np.dtype[np.int_]]

#: Number of outer walk-forward BLOCKS. Block 0 only seeds the first
#: training set, so this yields `DEFAULT_OUTER_SPLITS - 1` evaluated OOS
#: folds. Raised from 3 (which gave just 2 folds) because 2 is the bare
#: technical minimum `probability_of_backtest_overfitting()` accepts to form
#: a ranking at all, not a meaningful sample — and the CSCV/PBO machinery
#: assumes more paths than that in practice.
DEFAULT_OUTER_SPLITS = 6

DEFAULT_PARAM_GRID: tuple[dict[str, Any], ...] = (
    {"n_estimators": 50, "max_depth": 2, "learning_rate": 0.1},
    {"n_estimators": 100, "max_depth": 3, "learning_rate": 0.05},
    {"n_estimators": 50, "max_depth": 3, "learning_rate": 0.1},
)

#: XGBoost constructor arguments held FIXED across every fit — deliberately
#: NOT part of `DEFAULT_PARAM_GRID`, so they never inflate the trial count
#: that `deflated_sharpe_ratio()` deflates against.
#:
#: - `random_state` — without it, results are not reproducible and an
#:   upstream default change silently moves every recorded DSR.
#: - `n_jobs=-1` — use the machine; nothing here is latency-bound.
#: - `min_child_weight=5`, `gamma=0.1` — XGBoost's defaults (1 and 0) let a
#:   leaf form on a single row, far too permissive at the few-hundred
#:   training rows this project actually has.
#: - `subsample`/`colsample_bytree=0.8` — row/column stochasticity, the
#:   cheapest variance reduction available at this sample size.
#: - `reg_lambda=1.0` — explicit L2, pinned rather than inherited.
#: - `importance_type="gain"` — makes `feature_importances_` gain-based, which
#:   is what `TrainResult.feature_importances_per_outer_fold` reports.
#:
#: `use_label_encoder` is deliberately ABSENT: it was removed in XGBoost 2.x
#: and raises under this project's pinned `xgboost>=3.3`.
FIXED_XGB_PARAMS: dict[str, Any] = {
    "random_state": 42,
    "n_jobs": -1,
    "min_child_weight": 5,
    "gamma": 0.1,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "importance_type": "gain",
}


class ModelBackend(StrEnum):
    """Which library fits the classifier. The nested CV, purging, ledger
    recording, calibration and metrics are identical for all three — this
    switch changes ONLY the estimator and its hyperparameter grid, which is
    what makes a comparison between them meaningful."""

    XGBOOST = "xgboost"
    CATBOOST = "catboost"
    RANDOM_FOREST = "random_forest"
    #: A pretrained tabular foundation model, not a learner fitted from
    #: scratch. It is in this enum because it must be COMPARED on identical
    #: terms — same purged folds, same ledger, same calibration — but see
    #: `_make_tabicl` for the two ways it is genuinely different.
    TABICL = "tabicl"


#: CatBoost constructor arguments held FIXED across every fit — same
#: contract as `FIXED_XGB_PARAMS`: never swept, so they never inflate the
#: trial count `deflated_sharpe_ratio()` deflates against.
#:
#: - `boosting_type="Ordered"` is the whole reason CatBoost is here. Its
#:   ordered target statistics are what makes it the most overfit-resistant
#:   of the three below ~5,000 rows; the faster "Plain" mode would throw
#:   that away and leave a slower XGBoost.
#: - `random_seed=42` — reproducibility, mirroring XGBoost's `random_state`.
#: - `verbose=False` — this runs in a script, not a notebook.
#: - `allow_writing_files=False` — CatBoost otherwise scatters a
#:   `catboost_info/` directory wherever the process happens to be cwd'd.
#: - `min_data_in_leaf=5`, `l2_leaf_reg=3.0` — the same "a leaf may not form
#:   on a handful of rows" discipline as XGBoost's `min_child_weight=5`.
FIXED_CATBOOST_PARAMS: dict[str, Any] = {
    "random_seed": 42,
    "thread_count": -1,
    "verbose": False,
    "allow_writing_files": False,
    "boosting_type": "Ordered",
    "min_data_in_leaf": 5,
    "l2_leaf_reg": 3.0,
}

#: Random Forest constructor arguments held FIXED across every fit.
#:
#: - `class_weight="balanced"` is sklearn's equivalent of
#:   `scale_pos_weight`, and it is derived from the `y` handed to `fit()` —
#:   i.e. the FOLD's own labels, never the full dataset, which is the same
#:   no-leakage property `_scale_pos_weight` is careful about.
#: - `min_samples_leaf=5` — sklearn's default of 1 lets a leaf be a single
#:   row, far too permissive at a few hundred training rows.
#: - `max_features="sqrt"` — the column subsampling that makes a forest a
#:   forest rather than a bag of identical trees.
FIXED_RANDOM_FOREST_PARAMS: dict[str, Any] = {
    "random_state": 42,
    "n_jobs": -1,
    "class_weight": "balanced",
    "min_samples_leaf": 5,
    "max_features": "sqrt",
}

#: Each backend's grid is deliberately the SAME SIZE (3 configurations) so
#: no backend gets a wider search than another — a wider search finds a
#: better-looking number for free, and comparing an 8-config sweep against a
#: 3-config one would reward search breadth, not model quality.
CATBOOST_PARAM_GRID: tuple[dict[str, Any], ...] = (
    {"iterations": 50, "depth": 2, "learning_rate": 0.1},
    {"iterations": 100, "depth": 3, "learning_rate": 0.05},
    {"iterations": 50, "depth": 3, "learning_rate": 0.1},
)

#: Forests need many more trees than a boosted ensemble needs rounds (they
#: average rather than correct), and depth is the only real capacity knob
#: once `min_samples_leaf` is pinned.
RANDOM_FOREST_PARAM_GRID: tuple[dict[str, Any], ...] = (
    {"n_estimators": 200, "max_depth": 2},
    {"n_estimators": 400, "max_depth": 3},
    {"n_estimators": 200, "max_depth": 3},
)

#: TabICL constructor arguments held FIXED across every fit.
#:
#: - `random_state=42` — same reproducibility contract as the other three.
#: - `device=None` lets TabICL pick CUDA when present and fall back to CPU.
#:   This project runs CPU-only in Docker; the authors report a 50,000-row
#:   fit in under 10s on an H100, and CPU is materially slower but entirely
#:   workable at our ~2,600 rows.
#: - `allow_auto_download=True` — the pretrained checkpoint is fetched from
#:   HuggingFace on first use. Stated explicitly because it means the first
#:   run needs network access, unlike every other backend here.
FIXED_TABICL_PARAMS: dict[str, Any] = {
    "random_state": 42,
    "device": None,
    "allow_auto_download": True,
}

#: TabICL is PRETRAINED — there is no learning rate or tree depth to sweep,
#: because nothing is fitted. What these three vary is INFERENCE-time
#: ensembling: how many permuted views of the data get averaged, and how
#: sharply the resulting logits are turned into probabilities.
#:
#: Kept at three configurations like every other grid so no backend gets a
#: wider search than another (see `CATBOOST_PARAM_GRID`'s note). The
#: comparison is only meaningful if search breadth is held equal.
TABICL_PARAM_GRID: tuple[dict[str, Any], ...] = (
    {"n_estimators": 8, "softmax_temperature": 0.9},
    {"n_estimators": 16, "softmax_temperature": 0.9},
    {"n_estimators": 8, "softmax_temperature": 1.0},
)

#: Below this many training rows `_make_tabicl` REFUSES rather than returning
#: a number. The authors state TabICL is pretrained on datasets of 300-48,000
#: samples and that they "have not tested if TabICL generalizes to datasets
#: smaller than 300 samples" (soda-inria/tabicl). Independent benchmarking
#: additionally found fine-tuning it on small data collapses accuracy
#: (0.873 -> 0.567 on TabZilla), which is why this project uses it zero-shot
#: only and never fine-tunes it.
#:
#: A silent prediction from outside a model's documented range is exactly the
#: kind of unearned number this project exists to refuse — and it would be
#: invisible downstream, because an out-of-range TabICL still returns
#: well-formed probabilities.
MIN_TABICL_TRAINING_ROWS = 300

BACKEND_PARAM_GRIDS: dict[ModelBackend, tuple[dict[str, Any], ...]] = {
    ModelBackend.XGBOOST: DEFAULT_PARAM_GRID,
    ModelBackend.CATBOOST: CATBOOST_PARAM_GRID,
    ModelBackend.RANDOM_FOREST: RANDOM_FOREST_PARAM_GRID,
    ModelBackend.TABICL: TABICL_PARAM_GRID,
}

BACKEND_FIXED_PARAMS: dict[ModelBackend, dict[str, Any]] = {
    ModelBackend.XGBOOST: FIXED_XGB_PARAMS,
    ModelBackend.CATBOOST: FIXED_CATBOOST_PARAMS,
    ModelBackend.RANDOM_FOREST: FIXED_RANDOM_FOREST_PARAMS,
    ModelBackend.TABICL: FIXED_TABICL_PARAMS,
}

#: Preflight thresholds for `validate_training_set`.
MAX_ABS_FEATURE_LABEL_CORRELATION = 0.5
MIN_MINORITY_CLASS_FRACTION = 0.02
MIN_SAMPLES_PER_FEATURE = 15


@dataclass(frozen=True)
class TrainResult:
    model: MetaModel
    dsr: float
    pbo: PboResult
    n_trials_at_training: int
    n_labeled_samples: int
    best_params_per_outer_fold: tuple[dict[str, Any], ...]
    #: Gain-based feature importances per outer fold, index-aligned with
    #: `best_params_per_outer_fold`. Comparing folds exposes importance
    #: DRIFT — a feature that dominates one fold and vanishes in the next is
    #: unstable, which is invisible from DSR/PBO alone.
    feature_importances_per_outer_fold: tuple[dict[str, float], ...] = ()


class _Estimator(Protocol):
    """The only surface `train_meta_model` needs from a backend: fit it with
    optional sample weights, get probabilities out, and read per-feature
    importances (so `TrainResult.feature_importances_per_outer_fold` keeps
    working for all three). `get_params` is what
    `sklearn.calibration.CalibratedClassifierCV` needs to clone it."""

    feature_importances_: FloatArray

    def fit(self, x: FloatArray, y: IntArray, sample_weight: FloatArray | None = ...) -> object: ...

    def predict_proba(self, x: FloatArray) -> FloatArray: ...

    def get_params(self, deep: bool = ...) -> dict[str, Any]: ...


def _config_hash(config: dict[str, Any], backend: ModelBackend) -> str:
    """The backend is part of a trial's identity: `{"n_estimators": 20,
    "max_depth": 2}` describes a different model under XGBoost than under a
    Random Forest, and hashing them the same would make two genuinely
    distinct searches look like one config re-run."""
    return hashlib.sha256(json.dumps({"_backend": backend.value, **config}, sort_keys=True).encode()).hexdigest()


def _scale_pos_weight(y: IntArray) -> float:
    """`n_negative / n_positive` — the `scale_pos_weight` XGBoost and
    CatBoost both take (sklearn's forest uses `class_weight="balanced"`
    instead, which derives the same correction from the `y` it is fitted on).

    **Always computed on the FOLD's own training labels, never on the full
    dataset.** The full-dataset ratio summarises the test block's class
    distribution too, so using it would leak that information back across
    the split boundary. Returns `1.0` (no correction) for a degenerate
    single-class input rather than dividing by zero."""
    n_pos = int((y == 1).sum())
    n_neg = int((y == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return 1.0
    return n_neg / n_pos


def _make_xgb(config: dict[str, Any], y_train: IntArray) -> _Estimator:
    """An UNFITTED classifier for `config`, carrying `FIXED_XGB_PARAMS` and a
    `scale_pos_weight` derived from `y_train` alone."""
    estimator: _Estimator = XGBClassifier(
        objective="binary:logistic",
        eval_metric="logloss",
        scale_pos_weight=_scale_pos_weight(y_train),
        **FIXED_XGB_PARAMS,
        **config,
    )
    return estimator


def _make_catboost(config: dict[str, Any], y_train: IntArray) -> _Estimator:
    """CatBoost spells the imbalance correction exactly as XGBoost does, so
    `_scale_pos_weight` is reused verbatim — same fold-local computation,
    same no-leakage guarantee."""
    estimator: _Estimator = CatBoostClassifier(
        loss_function="Logloss",
        scale_pos_weight=_scale_pos_weight(y_train),
        **FIXED_CATBOOST_PARAMS,
        **config,
    )
    return estimator


def _make_random_forest(config: dict[str, Any], y_train: IntArray) -> _Estimator:
    """`y_train` is unused here on purpose: sklearn has no `scale_pos_weight`
    knob, and `class_weight="balanced"` (in `FIXED_RANDOM_FOREST_PARAMS`)
    already derives the same `n_neg/n_pos`-shaped correction from whatever
    `y` reaches `fit()` — which is this fold's training labels and nothing
    else. The parameter stays in the signature so all three constructors
    share one shape."""
    del y_train
    estimator: _Estimator = RandomForestClassifier(**FIXED_RANDOM_FOREST_PARAMS, **config)
    return estimator


class TabIclUnavailableError(RuntimeError):
    """`tabicl` is not installed. Its own name so a caller can tell "the
    optional dependency is missing" apart from "the model refused"."""


class TabIclDatasetTooSmallError(ValueError):
    """Fewer training rows than TabICL's authors have tested it on."""


def _make_tabicl(config: dict[str, Any], y_train: IntArray) -> _Estimator:
    """An UNFITTED `TabICLClassifier`, or a refusal.

    Two things make this backend genuinely different from the other three,
    and both are enforced here rather than left to the caller:

    1. **It is never fine-tuned.** `fit()` on a TabICL classifier stores the
       training rows as in-context examples; the transformer weights are the
       pretrained ones and stay frozen. That is deliberate — independent
       benchmarking found fine-tuning it on small data collapses accuracy
       (0.873 -> 0.567). There is consequently no fine-tuning switch exposed
       anywhere in this module.
    2. **It refuses small datasets** (`MIN_TABICL_TRAINING_ROWS`) instead of
       extrapolating outside its documented range. Note this is checked
       against the FOLD's training rows, not the whole dataset, because a
       dataset comfortably above the floor can still produce inner folds
       below it — and it is the fold that actually gets predicted from.

    `tabicl` is an OPTIONAL dependency (it pulls in `torch`, ~2GB installed),
    so the import is local: the engine's live path must not pay for a
    research-time model, and `import te.ml.train` has to keep working on an
    install that never asked for it.
    """
    try:
        from tabicl import TabICLClassifier
    except ImportError as exc:  # pragma: no cover — exercised by its own test via monkeypatch
        raise TabIclUnavailableError(
            "the 'tabicl' backend needs the optional dependency: pip install 'trading-engine[foundation]' "
            "(pulls in torch — see pyproject.toml for the CPU-wheel note)"
        ) from exc

    if len(y_train) < MIN_TABICL_TRAINING_ROWS:
        raise TabIclDatasetTooSmallError(
            f"TabICL refused a training fold of {len(y_train)} rows: its authors state it is pretrained on "
            f"300-48,000 samples and untested below {MIN_TABICL_TRAINING_ROWS}. Predicting from outside a "
            "model's documented range still returns well-formed probabilities, which is precisely why this "
            "refuses instead of warning."
        )

    estimator: _Estimator = TabICLClassifier(**FIXED_TABICL_PARAMS, **config)
    return estimator


_BACKEND_CONSTRUCTORS: dict[ModelBackend, Callable[[dict[str, Any], IntArray], _Estimator]] = {
    ModelBackend.XGBOOST: _make_xgb,
    ModelBackend.CATBOOST: _make_catboost,
    ModelBackend.RANDOM_FOREST: _make_random_forest,
    ModelBackend.TABICL: _make_tabicl,
}


def _make_estimator(backend: ModelBackend, config: dict[str, Any], y_train: IntArray) -> _Estimator:
    """An UNFITTED estimator of `backend` for `config`. `backend` is
    validated once at `train_meta_model`'s door (see `_resolve_backend`), so
    an unknown one never reaches this hot loop."""
    return _BACKEND_CONSTRUCTORS[backend](config, y_train)


def _resolve_backend(backend: ModelBackend | str) -> ModelBackend:
    """Raises on an unknown backend rather than silently falling back to
    XGBoost — a typo'd backend that quietly trained a different model than
    the caller asked for would mislabel every number it produced."""
    try:
        return ModelBackend(backend)
    except ValueError as exc:
        raise ValueError(
            f"unknown model backend {backend!r} — expected one of {[b.value for b in ModelBackend]}"
        ) from exc


def _config_key(config: dict[str, Any], backend: ModelBackend) -> str:
    """Stable identity for one hyperparameter configuration — sorted so two
    dicts with the same contents in a different insertion order are the same
    row of the CSCV matrix, not two. Carries the backend for the same reason
    `_config_hash` does; a single `train_meta_model` call only ever runs one
    backend, so this is documentation of that invariant rather than a
    behaviour change."""
    return json.dumps({"_backend": backend.value, **config}, sort_keys=True)


def _fit_estimator(
    backend: ModelBackend,
    config: dict[str, Any],
    x: FloatArray,
    y: IntArray,
    sample_weight: FloatArray | None = None,
) -> _Estimator:
    model = _make_estimator(backend, config, y)
    model.fit(x, y, sample_weight=sample_weight)
    return model


def validate_training_set(features: pd.DataFrame, labels: pd.Series) -> None:
    """Pre-training data-quality preflight. RAISES (never warns) — a dataset
    with any of these properties cannot produce a trustworthy model, so
    training it would only manufacture a number someone might believe.

    Three checks:

    1. **Feature/label correlation** above `MAX_ABS_FEATURE_LABEL_CORRELATION`
       at lag 0. A feature that strongly predicts the label at zero lag is
       far more often the label leaking into a feature than a real edge.
    2. **Severe class imbalance** — a minority class below
       `MIN_MINORITY_CLASS_FRACTION`. This is deliberately a much lower bar
       than `scale_pos_weight` (which is designed to handle moderate
       imbalance): below ~2% the problem is data INSUFFICIENCY, and no
       reweighting fixes having only a handful of positive examples.
    3. **Feature count** above `n_samples / MIN_SAMPLES_PER_FEATURE` — the
       standard overfitting-risk rule of thumb.

    Deliberately no stationarity (ADF) test: `statsmodels` is not a
    dependency of this project, and adding one for a single diagnostic is
    out of scope."""
    n_samples = len(features)
    n_features = len(features.columns)
    if n_samples == 0:
        raise ValueError("training set is empty")

    max_features = n_samples / MIN_SAMPLES_PER_FEATURE
    if n_features > max_features:
        raise ValueError(
            f"too many features for the sample size: {n_features} features on {n_samples} samples "
            f"exceeds the n_samples/{MIN_SAMPLES_PER_FEATURE} = {max_features:.1f} overfitting-risk limit"
        )

    label_values = labels.to_numpy(dtype=float)
    counts = pd.Series(label_values).value_counts(normalize=True)
    minority_fraction = float(counts.min()) if len(counts) > 1 else 0.0
    if minority_fraction < MIN_MINORITY_CLASS_FRACTION:
        raise ValueError(
            f"minority class is only {minority_fraction:.2%} of {n_samples} samples, below the "
            f"{MIN_MINORITY_CLASS_FRACTION:.0%} floor — this is data insufficiency, not something "
            "scale_pos_weight can correct"
        )

    for column in features.columns:
        series = features[column].to_numpy(dtype=float)
        if np.all(np.isnan(series)) or float(np.nanstd(series)) == 0.0:
            continue
        mask = ~np.isnan(series)
        if mask.sum() < 2:
            continue
        correlation = float(np.corrcoef(series[mask], label_values[mask])[0, 1])
        if not math.isnan(correlation) and abs(correlation) > MAX_ABS_FEATURE_LABEL_CORRELATION:
            raise ValueError(
                f"feature {column!r} has |correlation| {abs(correlation):.3f} with the label at lag 0, "
                f"above the {MAX_ABS_FEATURE_LABEL_CORRELATION} threshold — the label has very likely "
                "leaked into this feature"
            )


def _is_trainable(y: IntArray) -> bool:
    """A binary classifier cannot be fitted on a single-class target —
    XGBoost raises `ValueError: Invalid classes inferred from unique values
    of y`. Folds this returns `False` for are SKIPPED (never fitted, never
    scored, never recorded as a trial), which is the honest outcome: there
    is genuinely nothing to learn or measure there. Reachable on the
    earliest walk-forward folds, whose training block is the smallest."""
    return len(np.unique(y)) >= 2


def _safe_auc(y_true: IntArray, y_score: FloatArray) -> float:
    if len(set(y_true.tolist())) < 2:
        # A degenerate validation split (all-same-label) can't score AUC —
        # treat as a coin-flip trial rather than crashing the sweep.
        return 0.5
    return float(roc_auc_score(y_true, y_score))


def _outer_walk_forward_blocks(n: int, outer_splits: int) -> list[IntArray]:
    return [block for block in np.array_split(np.arange(n), outer_splits) if len(block) > 0]


def _per_observation_sharpe(returns: FloatArray) -> float:
    """`mean / std(ddof=1)` — the PER-OBSERVATION, un-annualized Sharpe
    convention. Identical by construction to `te.backtest.report.sharpe()`
    (kept as a local two-liner rather than an import, so `te.ml` does not
    acquire an edge onto `te.backtest`, which itself sits above `te.ml` and
    pulls in `te.strategy`/`te.risk`/`te.engine`).

    This is the ONLY Sharpe convention allowed anywhere near
    `te.ml.metrics`: `probabilistic_sharpe_ratio()` documents that `sr_hat`
    is "never annualized inside this formula", and
    `expected_max_sharpe_under_null()` needs its `mean_sharpe`/`var_sharpe`
    on the very same scale. `0.0` for a degenerate (fewer than 2
    observations, or zero-dispersion) series."""
    if len(returns) < 2:
        return 0.0
    std = float(returns.std(ddof=1))
    if std <= 0:
        return 0.0
    return float(returns.mean()) / std


def _agreement_returns(y_true: IntArray, calls: IntArray) -> FloatArray:
    """The module docstring's declared, deliberately-crude return proxy:
    `+1` where the model's above/below-0.5 call agrees with the realized
    triple-barrier label, `-1` otherwise. Not a claim about premium P&L —
    see the "Simplification, flagged clearly" note at the top of this
    module."""
    returns: FloatArray = np.where(calls == y_true, 1.0, -1.0).astype(float)
    return returns


def _max_label_horizon(prediction_times: pd.Series, evaluation_times: pd.Series) -> pd.Timedelta:
    """The longest label lifespan actually present in the data — i.e.
    `max_hold` from `te.ml.labeling`, recovered from the label windows
    themselves rather than re-passed in as a parameter that could silently
    drift out of sync with the data it describes. Triple-barrier labels that
    hit their target/stop early resolve sooner, so the MAX across rows is
    the horizon the purge has to defend against."""
    horizon = pd.to_datetime(evaluation_times) - pd.to_datetime(prediction_times)
    longest = horizon.max()
    if pd.isna(longest) or longest < pd.Timedelta(0):
        return pd.Timedelta(0)
    result: pd.Timedelta = longest
    return result


def outer_walk_forward_splits(
    *,
    prediction_times: pd.Series,
    evaluation_times: pd.Series,
    outer_splits: int,
) -> list[tuple[IntArray, IntArray]]:
    """The OUTER, expanding-window walk-forward splits — PURGED.

    `prediction_times`/`evaluation_times` must already be sorted by time and
    positionally indexed `0..n-1`. Block `0` seeds the first training set;
    every later block is evaluated once out-of-sample, so `outer_splits`
    blocks yield `outer_splits - 1` folds.

    **Why the purge is here and not optional.** These held-out blocks are
    the sole source of the reported DSR and PBO. An expanding window whose
    training set ends at exactly the test block's first row leaks: a
    training row near the boundary carries a triple-barrier label that only
    resolves up to `max_hold` LATER — i.e. inside the very window the model
    is about to be scored on. So each fold's training indices are passed
    through `te.ml.cv.purge_and_embargo` with `gap = max_hold` (the longest
    label lifespan actually present, per `_max_label_horizon`), NOT the
    span-proportional `te.ml.cv.DEFAULT_EMBARGO_PCT` — the leak is
    label-lifetime-shaped, so the gap is measured in label lifetimes.

    Folds whose training set purges down to empty are dropped rather than
    fitted on nothing."""
    n = len(prediction_times)
    blocks = _outer_walk_forward_blocks(n, outer_splits)
    gap = _max_label_horizon(prediction_times, evaluation_times)

    splits: list[tuple[IntArray, IntArray]] = []
    train_end = len(blocks[0]) if blocks else 0
    for block in blocks[1:]:
        raw_train_idx: IntArray = np.arange(0, train_end)
        purged_train_idx = purge_and_embargo(
            raw_train_idx,
            block,
            prediction_times=prediction_times,
            evaluation_times=evaluation_times,
            gap=gap,
        )
        train_end += len(block)
        if len(purged_train_idx) == 0:
            continue
        splits.append((purged_train_idx, block))
    return splits


def _run_inner_cv(
    *,
    x_train: FloatArray,
    y_train: IntArray,
    w_train: FloatArray | None,
    prediction_times: pd.Series,
    evaluation_times: pd.Series,
    param_grid: tuple[dict[str, Any], ...],
    inner_splits: int,
    trial_ledger: TrialLedger,
    run_id: str,
    backend: ModelBackend,
) -> dict[str, Any]:
    """Runs `PurgedKFold` inner CV over `x_train`/`y_train` for every config
    in `param_grid`, recording EVERY (config, split) trial to
    `trial_ledger`. Returns the best-scoring config by mean inner-CV AUC.

    **Two different statistics, deliberately.** AUC SELECTS the best
    hyperparameters (a ranking-quality measure is the right tool for that,
    and it is threshold-free). But what gets RECORDED into the ledger's
    `sharpe` column is a genuine return-based statistic
    (`_per_observation_sharpe` of the fold's `_agreement_returns`), because
    that column feeds `expected_max_sharpe_under_null()`, whose docstring
    requires "the mean and variance of the SHARPE RATIOS across all trials".
    Feeding AUCs there instead would put a bounded [0, 1] distribution
    clustered near 0.5 with tiny variance into a formula expecting
    Sharpe-scaled inputs, biasing the DSR null distribution."""
    n_splits = min(inner_splits, len(x_train))
    if n_splits < 2:
        # Not enough data to purge-split meaningfully — fall back to the
        # first config untested, still an honest (if degenerate) outcome.
        return dict(param_grid[0])

    splitter = PurgedKFold(n_splits, prediction_times=prediction_times, evaluation_times=evaluation_times)

    best_config: dict[str, Any] | None = None
    best_score = -np.inf
    for config in param_grid:
        scores: list[float] = []
        for train_idx, val_idx in splitter.split(x_train):
            if len(train_idx) == 0 or len(val_idx) == 0:
                continue
            if not _is_trainable(y_train[train_idx]):
                continue
            model = _fit_estimator(
                backend,
                config,
                x_train[train_idx],
                y_train[train_idx],
                None if w_train is None else w_train[train_idx],
            )
            preds = model.predict_proba(x_train[val_idx])[:, 1]
            score = _safe_auc(y_train[val_idx], preds)
            scores.append(score)

            # AUC ranks configs (above); a real return-based Sharpe goes in
            # the ledger (see this function's docstring).
            trial_returns = _agreement_returns(y_train[val_idx], (preds >= 0.5).astype(int))
            # Same `kind` for every backend, deliberately: a second model run
            # against this `run_id` ADDS to the N that DSR deflates against,
            # never starts a fresh count. See the module docstring.
            trial_ledger.record(
                kind=f"{run_id}:inner",
                config_hash=_config_hash(config, backend),
                sharpe=_per_observation_sharpe(trial_returns),
                run_id=run_id,
            )
        mean_score = float(np.mean(scores)) if scores else 0.5
        if mean_score > best_score:
            best_score = mean_score
            best_config = config

    assert best_config is not None
    return best_config


def train_meta_model(
    *,
    features: pd.DataFrame,
    labels: pd.Series,
    prediction_times: pd.Series,
    evaluation_times: pd.Series,
    spec: FeatureSpec,
    trial_ledger: TrialLedger,
    run_id: str,
    sample_weights: pd.Series | None = None,
    outer_splits: int = DEFAULT_OUTER_SPLITS,
    inner_splits: int = 3,
    param_grid: tuple[dict[str, Any], ...] | None = None,
    backend: ModelBackend = ModelBackend.XGBOOST,
) -> TrainResult:
    """Nested-CV trains a `MetaModel` for `spec`. `features`/`labels`/
    `prediction_times`/`evaluation_times` must share the same index, sorted
    (or sortable) by time. Every inner-loop trial is recorded to
    `trial_ledger`; DSR/PBO are computed only from the outer loop's held-out
    blocks, which are PURGED against the training set (see
    `outer_walk_forward_splits`).

    `sample_weights`, when given, must share `features`' index — these are
    `te.ml.labeling`'s Lopez de Prado uniqueness weights
    (`LabeledFiring.weight`), and they are threaded into BOTH the model fits
    (whichever `backend`) and the Platt calibrator's fit. Overlapping triple-barrier labels
    are not independent observations; without these weights, periods with
    many concurrent labels are silently over-counted.

    `backend` picks the estimator (`ModelBackend.XGBOOST` by default, which
    is what every pre-existing caller gets and what every number recorded to
    date came from). `param_grid` defaults to THAT backend's own grid —
    passing XGBoost's `n_estimators`/`max_depth` keywords to CatBoost would
    simply raise — and every backend's grid is the same size so none gets a
    wider search than another. All three record into the same
    `f"{run_id}:inner"` ledger scope, so comparing them raises the DSR bar
    for all of them, which is the point.

    `validate_training_set` runs first, before any CV or fitting."""
    backend = _resolve_backend(backend)
    grid = param_grid if param_grid is not None else BACKEND_PARAM_GRIDS[backend]
    if not list(features.columns) == list(spec.columns):
        raise ValueError(
            f"features columns {list(features.columns)!r} must exactly match spec.columns {spec.columns!r}"
        )
    if sample_weights is not None and not sample_weights.index.equals(features.index):
        raise ValueError(
            "sample_weights must share the same index as features "
            f"({len(sample_weights)} weights vs {len(features)} feature rows)"
        )

    order = prediction_times.sort_values().index
    x_all = features.loc[order].to_numpy(dtype=float)
    y_all = labels.loc[order].to_numpy(dtype=int)
    w_all: FloatArray | None = None if sample_weights is None else sample_weights.loc[order].to_numpy(dtype=float)
    pred_times = prediction_times.loc[order].reset_index(drop=True)
    eval_times = evaluation_times.loc[order].reset_index(drop=True)

    n = len(order)
    blocks = _outer_walk_forward_blocks(n, outer_splits)
    if len(blocks) < 2:
        raise ValueError(
            f"not enough samples ({n}) to form >=2 outer walk-forward blocks with outer_splits={outer_splits}"
        )

    # Data-quality preflight — after the cheap shape/argument guards above
    # (so a degenerate n reports the specific "not enough samples" error
    # rather than a confusing feature-count complaint), but before any CV
    # splitting or fitting happens.
    validate_training_set(features, labels)

    outer_splits_idx = outer_walk_forward_splits(
        prediction_times=pred_times, evaluation_times=eval_times, outer_splits=outer_splits
    )
    if not outer_splits_idx:
        raise ValueError(
            f"every outer walk-forward fold purged down to an empty training set for n={n}, "
            f"outer_splits={outer_splits} — the label horizon is too long relative to the block size"
        )

    best_params_per_fold: list[dict[str, Any]] = []
    importances_per_fold: list[dict[str, float]] = []
    outer_returns: list[list[float]] = []
    #: config key -> its OOS return series, concatenated across outer folds
    #: in fold order. Every config sees the SAME test blocks in the same
    #: order, which is exactly the shared period grid CSCV requires.
    per_config_returns: dict[str, list[float]] = {}
    final_model: MetaModel | None = None

    for train_idx, test_idx in outer_splits_idx:
        if not _is_trainable(y_all[train_idx]):
            # Nothing to learn from a single-class training block — skip
            # rather than crash or fabricate a fold. See `_is_trainable`.
            continue

        fold_pred_times = pred_times.iloc[train_idx].reset_index(drop=True)
        fold_eval_times = eval_times.iloc[train_idx].reset_index(drop=True)
        w_fold: FloatArray | None = None if w_all is None else w_all[train_idx]

        best_config = _run_inner_cv(
            x_train=x_all[train_idx],
            y_train=y_all[train_idx],
            w_train=w_fold,
            prediction_times=fold_pred_times,
            evaluation_times=fold_eval_times,
            param_grid=grid,
            inner_splits=inner_splits,
            trial_ledger=trial_ledger,
            run_id=run_id,
            backend=backend,
        )
        best_params_per_fold.append(best_config)

        raw_model = _fit_estimator(backend, best_config, x_all[train_idx], y_all[train_idx], w_fold)
        importances_per_fold.append(
            {name: float(value) for name, value in zip(spec.columns, raw_model.feature_importances_, strict=True)}
        )

        calibration_splits = min(inner_splits, len(train_idx))
        if calibration_splits >= 2:
            calibrated = fit_platt_calibrator(
                _make_estimator(backend, best_config, y_all[train_idx]),
                x_all[train_idx],
                y_all[train_idx],
                sample_weight=w_fold,
                cv=PurgedKFold(
                    calibration_splits,
                    prediction_times=fold_pred_times,
                    evaluation_times=fold_eval_times,
                ),
            )
        else:
            calibrated = fit_platt_calibrator(
                raw_model, x_all[train_idx], y_all[train_idx], sample_weight=w_fold, n_splits=2
            )

        test_preds = calibrated.predict_proba(x_all[test_idx])[:, 1]
        test_calls = (test_preds >= 0.5).astype(int)
        outer_returns.append(_agreement_returns(y_all[test_idx], test_calls).tolist())

        # Every CONFIG's OOS series on this same test block, not just the
        # winner's. This is what makes the PBO below a real one: CSCV is
        # defined over COMPETING CONFIGURATIONS ranked in-sample against
        # out-of-sample, and feeding it per-fold series of a single config
        # (as this did until 2026-08-01) answers a different question
        # entirely — which is why the number had to be printed as "NOT
        # EVALUATED". Costs |grid| extra fits per outer fold.
        #
        # One call runs ONE backend, so this matrix's competing rows are
        # always same-backend configurations and the resulting PBO answers
        # "was this backend's chosen config curve-fit?". Stacking two
        # backends' series into one matrix would answer a different question
        # (is the MODEL CHOICE curve-fit?) and must not be done silently.
        for config in grid:
            key = _config_key(config, backend)
            model = _fit_estimator(backend, config, x_all[train_idx], y_all[train_idx], w_fold)
            calls = (model.predict_proba(x_all[test_idx])[:, 1] >= 0.5).astype(int)
            per_config_returns.setdefault(key, []).extend(
                _agreement_returns(y_all[test_idx], calls).tolist()
            )

        final_model = MetaModel(calibrated=calibrated, feature_spec=spec)

    if final_model is None:
        raise ValueError(
            f"no outer walk-forward fold was trainable for n={n}, outer_splits={outer_splits} — "
            "every fold's training block was empty after purging or contained a single class"
        )

    flat_returns = np.array([r for fold in outer_returns for r in fold], dtype=float)
    sr_hat = _per_observation_sharpe(flat_returns)

    n_trials = trial_ledger.n_trials(f"{run_id}:inner")
    # These are per-observation Sharpes of real return series (see
    # `_run_inner_cv`), on exactly the same scale as `sr_hat` above — which
    # is what `expected_max_sharpe_under_null()` requires of them.
    trial_sharpes = trial_ledger.trial_sharpes(f"{run_id}:inner")
    mean_sharpe = float(np.mean(trial_sharpes)) if trial_sharpes else 0.0
    var_sharpe = float(np.var(trial_sharpes, ddof=1)) if len(trial_sharpes) > 1 else 0.0

    dsr = deflated_sharpe_ratio(
        sr_hat=sr_hat,
        n_obs=len(flat_returns),
        skewness=float(skew(flat_returns)) if len(flat_returns) > 2 else 0.0,
        kurtosis=float(kurtosis(flat_returns, fisher=False)) if len(flat_returns) > 2 else 3.0,
        mean_sharpe=mean_sharpe,
        var_sharpe=var_sharpe,
        n_trials=max(n_trials, 1),
    )

    # A REAL CSCV matrix: one row per competing CONFIGURATION, all over the
    # same OOS period grid (see `per_config_returns`). Every row has
    # identical length by construction — each config is scored on every
    # outer test block — so no padding or truncation is needed, and the
    # ranking CSCV performs compares things that are genuinely comparable.
    #
    # Previously this stacked the per-outer-FOLD series of a single config,
    # which ranks time periods against each other rather than strategies.
    # That number was correctly refused as a gate by
    # `scripts/train_meta_model.py`; this one can be gated on.
    series = [r for r in per_config_returns.values() if len(r) >= 2]
    lengths = {len(r) for r in series}
    if len(series) >= 2 and len(lengths) == 1:
        n_periods = next(iter(lengths))
        returns_matrix = np.array(series)
        pbo = probability_of_backtest_overfitting(
            returns_matrix, s_groups=min(8, n_periods - (n_periods % 2) or 2)
        )
    else:
        # Fewer than 2 usable configs, or (defensively) ragged series —
        # NaN, never a fabricated 0.0, which would read as "no overfitting".
        pbo = PboResult(pbo=float("nan"), n_combinations=0)

    return TrainResult(
        model=final_model,
        dsr=dsr,
        pbo=pbo,
        n_trials_at_training=n_trials,
        n_labeled_samples=n,
        best_params_per_outer_fold=tuple(best_params_per_fold),
        feature_importances_per_outer_fold=tuple(importances_per_fold),
    )
