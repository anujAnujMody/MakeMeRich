"""`te.ml.train.train_meta_model` — nested CV ordering: outer walk-forward
reports metrics, inner PurgedKFold tunes hyperparameters, every inner trial
recorded to `TrialLedger`."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa

from te.domain.clock import IST
from te.ml.featurespec import SECONDARY_V1
from te.ml.train import DEFAULT_PARAM_GRID, train_meta_model
from te.ml.trials import TrialLedger


@pytest.fixture
def trial_ledger(tmp_path: Path) -> TrialLedger:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'trials.db'}")
    return TrialLedger(engine)


def _synthetic_dataset(n: int = 200, seed: int = 0) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    base_ts = dt.datetime(2026, 6, 1, 9, 30, tzinfo=IST)
    prediction_times = pd.Series([base_ts + dt.timedelta(minutes=5 * i) for i in range(n)])
    evaluation_times = pd.Series([t + dt.timedelta(minutes=15) for t in prediction_times])

    features = pd.DataFrame({col: rng.normal(size=n) for col in SECONDARY_V1.columns})
    # Label WEAKLY related to one feature, so the model has *something* to
    # learn while keeping this a structural test of the CV plumbing, not a
    # claim of edge. Deliberately noisy: a label defined as a clean threshold
    # on a feature (the earlier `features["dte"] > 0`) correlates ~0.8 with
    # that feature and is exactly what `validate_training_set`'s leakage
    # check exists to reject.
    latent = 0.6 * features["dte"] + rng.normal(scale=1.5, size=n)
    labels = pd.Series((latent > 0).astype(int))
    return features, labels, prediction_times, evaluation_times


def test_train_meta_model_records_every_inner_trial(trial_ledger: TrialLedger) -> None:
    features, labels, pred_times, eval_times = _synthetic_dataset()

    before = trial_ledger.n_trials("run-1:inner")
    assert before == 0

    result = train_meta_model(
        features=features,
        labels=labels,
        prediction_times=pred_times,
        evaluation_times=eval_times,
        spec=SECONDARY_V1,
        trial_ledger=trial_ledger,
        run_id="run-1",
        outer_splits=3,
        inner_splits=3,
    )

    after = trial_ledger.n_trials("run-1:inner")
    # 2 outer folds (outer_splits=3 -> 1 train block + 2 test blocks) x
    # len(DEFAULT_PARAM_GRID) configs x up to inner_splits inner splits each.
    assert after > 0
    assert after == result.n_trials_at_training


def test_train_meta_model_returns_a_fitted_model_and_metrics(trial_ledger: TrialLedger) -> None:
    features, labels, pred_times, eval_times = _synthetic_dataset(n=300)

    result = train_meta_model(
        features=features,
        labels=labels,
        prediction_times=pred_times,
        evaluation_times=eval_times,
        spec=SECONDARY_V1,
        trial_ledger=trial_ledger,
        run_id="run-2",
    )

    assert result.model.feature_spec == SECONDARY_V1
    assert isinstance(result.dsr, float)
    assert result.pbo.n_combinations >= 0
    assert result.n_labeled_samples == 300
    # DEFAULT_OUTER_SPLITS=6 -> block 0 seeds the training set, 5 OOS folds.
    assert len(result.best_params_per_outer_fold) == 5

    row = features.iloc[-1]
    p = result.model.predict_proba(row)
    assert 0.0 <= p <= 1.0


def test_train_meta_model_rejects_mismatched_feature_columns(trial_ledger: TrialLedger) -> None:
    features, labels, pred_times, eval_times = _synthetic_dataset()
    bad_features = features.rename(columns={"dte": "not_dte"})

    with pytest.raises(ValueError, match="must exactly match"):
        train_meta_model(
            features=bad_features,
            labels=labels,
            prediction_times=pred_times,
            evaluation_times=eval_times,
            spec=SECONDARY_V1,
            trial_ledger=trial_ledger,
            run_id="run-3",
        )


def test_train_meta_model_raises_on_too_few_samples_for_outer_splits(trial_ledger: TrialLedger) -> None:
    features, labels, pred_times, eval_times = _synthetic_dataset(n=1)
    with pytest.raises(ValueError, match="not enough samples"):
        train_meta_model(
            features=features,
            labels=labels,
            prediction_times=pred_times,
            evaluation_times=eval_times,
            spec=SECONDARY_V1,
            trial_ledger=trial_ledger,
            run_id="run-4",
            outer_splits=5,
        )


def test_trial_ledger_records_return_based_sharpes_not_aucs(trial_ledger: TrialLedger) -> None:
    """Regression guard: the ledger's `sharpe` column feeds
    `expected_max_sharpe_under_null()`, which documents that it needs "the
    mean and variance of the SHARPE RATIOS across all trials". It used to be
    handed inner-CV AUCs instead — a bounded [0, 1] distribution clustered
    near 0.5 with tiny variance, on a completely different scale from
    `sr_hat`, biasing the DSR null distribution.

    An AUC can never be negative and is very unlikely to exceed 1.0; a
    per-observation Sharpe of a +1/-1 agreement series is unbounded in both
    directions and is essentially never confined to [0, 1] across a whole
    sweep. So: assert the recorded values are NOT AUC-shaped."""
    features, labels, pred_times, eval_times = _synthetic_dataset(n=200, seed=3)

    train_meta_model(
        features=features,
        labels=labels,
        prediction_times=pred_times,
        evaluation_times=eval_times,
        spec=SECONDARY_V1,
        trial_ledger=trial_ledger,
        run_id="run-sharpe",
    )

    recorded = trial_ledger.trial_sharpes("run-sharpe:inner")
    assert len(recorded) > 0

    # A recorded value outside [0, 1] is impossible for an AUC and routine
    # for a Sharpe — its presence proves the column is no longer AUC.
    assert any(v < 0.0 or v > 1.0 for v in recorded), (
        f"every recorded trial statistic landed inside [0, 1] ({recorded!r}) — "
        "this is AUC-shaped, not Sharpe-shaped"
    )


def test_recorded_trial_sharpe_matches_the_per_observation_convention() -> None:
    """`_per_observation_sharpe` must be exactly `mean / std(ddof=1)`, the
    same convention as `te.backtest.report.sharpe()` and the one
    `te.ml.metrics` documents — no sqrt(n), no annualization."""
    from te.backtest.report import sharpe as report_sharpe
    from te.ml.train import _per_observation_sharpe

    returns = np.array([1.0, -1.0, 1.0, 1.0, -1.0, 1.0, 1.0, -1.0], dtype=float)
    assert _per_observation_sharpe(returns) == pytest.approx(float(returns.mean() / returns.std(ddof=1)))
    assert _per_observation_sharpe(returns) == pytest.approx(report_sharpe([int(r) for r in returns]))


def test_default_outer_splits_yields_five_evaluated_oos_folds(trial_ledger: TrialLedger) -> None:
    """Raised from 3 blocks (2 OOS folds — PBO's bare technical minimum) to
    6 blocks (5 OOS folds)."""
    from te.ml.train import DEFAULT_OUTER_SPLITS

    assert DEFAULT_OUTER_SPLITS == 6

    features, labels, pred_times, eval_times = _synthetic_dataset(n=300, seed=5)
    result = train_meta_model(
        features=features,
        labels=labels,
        prediction_times=pred_times,
        evaluation_times=eval_times,
        spec=SECONDARY_V1,
        trial_ledger=trial_ledger,
        run_id="run-folds",
    )
    assert len(result.best_params_per_outer_fold) == 5


def test_default_param_grid_never_uses_lightgbm() -> None:
    import te.ml.train as train_module

    assert train_module.__file__ is not None
    with Path(train_module.__file__).open(encoding="utf-8") as f:
        source = f.read()
    assert "import lightgbm" not in source
    assert "from lightgbm" not in source


# ---------------------------------------------------------------------------
# Reproducibility + regularization (fixed XGBoost constructor arguments)
# ---------------------------------------------------------------------------


def test_xgb_constructor_pins_reproducibility_and_regularization_params() -> None:
    """Without a pinned `random_state` every recorded DSR silently changes if
    an upstream XGBoost default moves; without regularization the defaults
    (notably `min_child_weight=1`) are far too permissive at the few-hundred
    training rows this project has."""
    from te.ml.train import FIXED_XGB_PARAMS

    assert FIXED_XGB_PARAMS["random_state"] == 42
    assert FIXED_XGB_PARAMS["n_jobs"] == -1
    assert FIXED_XGB_PARAMS["min_child_weight"] == 5
    assert FIXED_XGB_PARAMS["gamma"] == 0.1
    assert FIXED_XGB_PARAMS["subsample"] == 0.8
    assert FIXED_XGB_PARAMS["colsample_bytree"] == 0.8
    assert FIXED_XGB_PARAMS["reg_lambda"] == 1.0

    # `use_label_encoder` was REMOVED in XGBoost 2.x and raises under this
    # project's pinned `xgboost>=3.3`.
    assert "use_label_encoder" not in FIXED_XGB_PARAMS

    # The swept grid must stay disjoint from the fixed arguments, or the
    # constructor would receive duplicate keyword arguments.
    for config in DEFAULT_PARAM_GRID:
        assert not set(config) & set(FIXED_XGB_PARAMS)


def test_training_is_reproducible_across_identical_runs(tmp_path: Path) -> None:
    features, labels, pred_times, eval_times = _synthetic_dataset(n=300, seed=11)

    def _run(tag: str) -> float:
        engine = sa.create_engine(f"sqlite:///{tmp_path / (tag + '.db')}")
        result = train_meta_model(
            features=features, labels=labels, prediction_times=pred_times, evaluation_times=eval_times,
            spec=SECONDARY_V1, trial_ledger=TrialLedger(engine), run_id=tag,
        )
        return result.model.predict_proba(features.iloc[-1])

    assert _run("rep-a") == pytest.approx(_run("rep-b"))


# ---------------------------------------------------------------------------
# Sample uniqueness weights (Lopez de Prado) actually reach the model
# ---------------------------------------------------------------------------


def test_sample_weights_are_passed_through_to_the_fitted_model(trial_ledger: TrialLedger) -> None:
    """`te.ml.labeling` computes uniqueness weights onto every
    `LabeledFiring.weight` and documents them as feeding "sample weights and
    purge/embargo logic" — but they used to be discarded: `train_meta_model`
    took no weight parameter and `model.fit(x, y)` never passed
    `sample_weight`. Weighting must measurably change the fitted model."""
    features, labels, pred_times, eval_times = _synthetic_dataset(n=300, seed=13)

    unweighted = train_meta_model(
        features=features, labels=labels, prediction_times=pred_times, evaluation_times=eval_times,
        spec=SECONDARY_V1, trial_ledger=trial_ledger, run_id="w-off",
    )

    # Strongly non-uniform weights, index-aligned with `features`.
    weights = pd.Series(np.linspace(0.01, 1.0, len(features)), index=features.index)
    weighted = train_meta_model(
        features=features, labels=labels, prediction_times=pred_times, evaluation_times=eval_times,
        spec=SECONDARY_V1, trial_ledger=trial_ledger, run_id="w-on", sample_weights=weights,
    )

    unweighted_p = unweighted.model.predict_proba(features.iloc[-1])
    weighted_p = weighted.model.predict_proba(features.iloc[-1])
    assert unweighted_p != pytest.approx(weighted_p)


def test_sample_weights_must_be_index_aligned_with_features(trial_ledger: TrialLedger) -> None:
    features, labels, pred_times, eval_times = _synthetic_dataset(n=120, seed=17)
    misaligned = pd.Series([1.0] * 10)

    with pytest.raises(ValueError, match="sample_weights"):
        train_meta_model(
            features=features, labels=labels, prediction_times=pred_times, evaluation_times=eval_times,
            spec=SECONDARY_V1, trial_ledger=trial_ledger, run_id="w-bad", sample_weights=misaligned,
        )


def test_scale_pos_weight_is_computed_inside_the_fold_never_on_the_full_dataset() -> None:
    """Class-imbalance correction computed across the whole dataset would
    leak the test block's class distribution back across the split."""
    from te.ml.train import _scale_pos_weight

    assert _scale_pos_weight(np.array([0, 1, 0, 1], dtype=int)) == pytest.approx(1.0)
    assert _scale_pos_weight(np.array([0, 0, 0, 0, 0, 0, 1, 1], dtype=int)) == pytest.approx(3.0)
    # Degenerate single-class input must not divide by zero.
    assert _scale_pos_weight(np.array([1, 1, 1], dtype=int)) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Pre-training data-quality preflight
# ---------------------------------------------------------------------------


def _clean_training_set(n: int = 400) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(0)
    features = pd.DataFrame({col: rng.normal(size=n) for col in SECONDARY_V1.columns})
    labels = pd.Series(rng.integers(0, 2, size=n))
    return features, labels


def test_validate_training_set_accepts_a_healthy_dataset() -> None:
    from te.ml.train import validate_training_set

    features, labels = _clean_training_set()
    validate_training_set(features, labels)  # must not raise


def test_validate_training_set_raises_on_a_feature_correlated_with_the_label() -> None:
    from te.ml.train import validate_training_set

    features, labels = _clean_training_set()
    # A feature that is essentially the label — the classic "the label leaked
    # into a feature" smell.
    features["dte"] = labels.astype(float) + np.random.default_rng(1).normal(scale=0.05, size=len(labels))

    with pytest.raises(ValueError, match="correlat"):
        validate_training_set(features, labels)


def test_validate_training_set_raises_on_severe_class_imbalance() -> None:
    from te.ml.train import validate_training_set

    features, _ = _clean_training_set(n=400)
    labels = pd.Series([0] * 396 + [1] * 4)  # 1% minority — below what a weight can fix

    with pytest.raises(ValueError, match="minority class"):
        validate_training_set(features, labels)


def test_validate_training_set_raises_when_features_outnumber_samples_over_15_to_1() -> None:
    from te.ml.train import validate_training_set

    # 7 features needs >= 105 samples under the n_samples / 15 rule of thumb.
    features, labels = _clean_training_set(n=60)

    with pytest.raises(ValueError, match="too many features"):
        validate_training_set(features, labels)


def test_train_meta_model_runs_the_preflight_before_any_fitting(trial_ledger: TrialLedger) -> None:
    features, labels, pred_times, eval_times = _synthetic_dataset(n=300, seed=19)
    features["dte"] = labels.astype(float)  # label leaked into a feature

    with pytest.raises(ValueError, match="correlat"):
        train_meta_model(
            features=features, labels=labels, prediction_times=pred_times, evaluation_times=eval_times,
            spec=SECONDARY_V1, trial_ledger=trial_ledger, run_id="preflight",
        )


# ---------------------------------------------------------------------------
# Per-fold feature importance
# ---------------------------------------------------------------------------


def test_train_result_captures_per_fold_feature_importances(trial_ledger: TrialLedger) -> None:
    """Importance drift across walk-forward folds is a real signal of feature
    instability, and was previously invisible."""
    features, labels, pred_times, eval_times = _synthetic_dataset(n=300, seed=23)

    result = train_meta_model(
        features=features, labels=labels, prediction_times=pred_times, evaluation_times=eval_times,
        spec=SECONDARY_V1, trial_ledger=trial_ledger, run_id="importances",
    )

    importances = result.feature_importances_per_outer_fold
    assert len(importances) == len(result.best_params_per_outer_fold)
    for fold in importances:
        assert set(fold) == set(SECONDARY_V1.columns)
        assert all(v >= 0.0 for v in fold.values())
