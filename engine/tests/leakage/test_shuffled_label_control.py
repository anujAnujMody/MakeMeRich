"""Shuffled-label control — the standing invariant that the CV/leakage
machinery itself doesn't leak signal. Full ML model construction (the real
meta-labeling classifier, feature spec, dataset builder) is explicitly
Phase 6's job, not this one; per the plan's own escape hatch this test runs
a MINIMAL toy pipeline — synthetic features, a plain `LogisticRegression`,
`te.ml.cv.PurgedKFold` — whose sole purpose is proving the CV machinery
built in this phase doesn't leak: if the CV/purge/embargo code were broken
(e.g. it accidentally let future information into training), a classifier
trained on genuinely SHUFFLED (label-signal-destroyed) labels would still
score above-chance OOS AUC.

Approach taken: **toy discriminator over synthetic features**, not the real
ORB->meta-label pipeline (out of scope this phase — see the module
docstring and the task's point 6).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from te.ml.cv import PurgedKFold

N_SAMPLES = 300
N_FEATURES = 4

FloatArray = np.ndarray[tuple[int, ...], np.dtype[np.float64]]
IntArray = np.ndarray[tuple[int, ...], np.dtype[np.int64]]


def _synthetic_dataset(rng: np.random.Generator) -> tuple[FloatArray, IntArray, pd.Series, pd.Series]:
    features = rng.normal(size=(N_SAMPLES, N_FEATURES))
    prediction_times = pd.Series(pd.date_range("2026-01-01", periods=N_SAMPLES, freq="D"))
    evaluation_times = prediction_times + pd.Timedelta(days=2)  # a real (non-trivial) label horizon
    true_signal_labels = (
        features[:, 0] * 1.5 - features[:, 1] * 0.8 + rng.normal(scale=0.5, size=N_SAMPLES)
    ) > 0
    return features, true_signal_labels.astype(int), prediction_times, evaluation_times


def _oos_auc(
    features: FloatArray, labels: IntArray, prediction_times: pd.Series, evaluation_times: pd.Series
) -> float:
    cv = PurgedKFold(n_splits=5, prediction_times=prediction_times, evaluation_times=evaluation_times)
    oos_true: list[int] = []
    oos_pred: list[float] = []
    for train_idx, test_idx in cv.split(features):
        if len(np.unique(labels[train_idx])) < 2:
            continue  # a degenerate all-one-class fold can't fit/score — skip, matches real training code
        clf = LogisticRegression()
        clf.fit(features[train_idx], labels[train_idx])
        proba = clf.predict_proba(features[test_idx])[:, 1]
        oos_true.extend(labels[test_idx].tolist())
        oos_pred.extend(proba.tolist())
    return float(roc_auc_score(oos_true, oos_pred))


def test_shuffled_labels_yield_near_chance_oos_auc() -> None:
    rng = np.random.default_rng(0)
    features, true_labels, prediction_times, evaluation_times = _synthetic_dataset(rng)
    shuffled_labels = rng.permutation(true_labels)

    auc = _oos_auc(features, shuffled_labels, prediction_times, evaluation_times)

    assert 0.45 <= auc <= 0.55, auc


def test_true_signal_labels_are_discriminable_sanity_check() -> None:
    """Not the golden invariant itself, but proof the toy pipeline actually
    has power when the label isn't destroyed — otherwise the shuffled-label
    test above would trivially pass at ~0.5 for the wrong reason (a broken/
    inert pipeline, not a leak-free one)."""
    rng = np.random.default_rng(0)
    features, true_labels, prediction_times, evaluation_times = _synthetic_dataset(rng)

    auc = _oos_auc(features, true_labels, prediction_times, evaluation_times)

    assert auc > 0.8, auc


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_shuffled_labels_near_chance_across_seeds(seed: int) -> None:
    rng = np.random.default_rng(seed)
    features, true_labels, prediction_times, evaluation_times = _synthetic_dataset(rng)
    shuffled_labels = rng.permutation(true_labels)

    auc = _oos_auc(features, shuffled_labels, prediction_times, evaluation_times)

    assert 0.4 <= auc <= 0.6, auc
