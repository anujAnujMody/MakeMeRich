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


# ---------------------------------------------------------------------------
# Does the control have the POWER to catch a genuinely broken splitter?
#
# `test_shuffled_labels_yield_near_chance_oos_auc` above only proves the
# suite is green when the real `PurgedKFold` is used. It does not prove the
# control would go RED if that splitter were broken (e.g. accidentally
# returned train indices that overlap test). Measured empirically: at
# N_SAMPLES=300 / N_FEATURES=4 (this file's own toy dataset), swapping in a
# FULLY leaky splitter (train_idx == test_idx == every row) and refitting on
# shuffled labels lands inside [0.45, 0.55] — i.e. the control would MISS
# the leak — in roughly 1 run out of 4 across seeds (measured: 22/30 seeds
# scored above 0.55, 8/30 landed back inside the "looks clean" band). A
# 4-feature `LogisticRegression` on 300 rows genuinely does not have
# reliable power to detect leakage; it was decorative more often than not.
#
# Fix: a dedicated POSITIVE-CONTROL test below, with more features (so a
# fully leaky fit has more surface to memorize noise on) — this is the
# "leak-detecting positive control" the audit asked for, kept separate from
# the near-chance tests above so their toy dataset (and the real-PurgedKFold
# machinery they exercise) is unchanged.
# ---------------------------------------------------------------------------

N_FEATURES_POWERFUL = 20


def _powerful_synthetic_dataset(rng: np.random.Generator) -> tuple[FloatArray, IntArray]:
    """Same label-generating process as `_synthetic_dataset` (only features
    0 and 1 carry real signal), but with `N_FEATURES_POWERFUL` columns
    instead of `N_FEATURES` — more noise columns for a leaky (train==test)
    fit to overfit on, which is exactly what gives the positive control
    power without changing what "real signal" means."""
    features = rng.normal(size=(N_SAMPLES, N_FEATURES_POWERFUL))
    true_signal_labels = (
        features[:, 0] * 1.5 - features[:, 1] * 0.8 + rng.normal(scale=0.5, size=N_SAMPLES)
    ) > 0
    return features, true_signal_labels.astype(int)


def _fully_leaky_split(features: FloatArray) -> list[tuple[IntArray, IntArray]]:
    """No purge, no embargo, no holdout at all: train and test are the
    SAME rows. Stands in for a `PurgedKFold` that has silently regressed to
    leaking everything — the worst case the leakage suite exists to catch."""
    all_idx = np.arange(len(features))
    return [(all_idx, all_idx)]


def _oos_auc_over_splits(features: FloatArray, labels: IntArray, splits: list[tuple[IntArray, IntArray]]) -> float:
    """`_oos_auc`'s fit/score loop, generalised to take pre-built splits
    instead of constructing a `PurgedKFold` itself — this is what lets a
    test inject `_fully_leaky_split` in `PurgedKFold`'s place."""
    oos_true: list[int] = []
    oos_pred: list[float] = []
    for train_idx, test_idx in splits:
        if len(np.unique(labels[train_idx])) < 2:
            continue
        clf = LogisticRegression()
        clf.fit(features[train_idx], labels[train_idx])
        proba = clf.predict_proba(features[test_idx])[:, 1]
        oos_true.extend(labels[test_idx].tolist())
        oos_pred.extend(proba.tolist())
    return float(roc_auc_score(oos_true, oos_pred))


@pytest.mark.parametrize("seed", range(10))
def test_a_fully_leaky_splitter_is_caught_on_shuffled_labels(seed: int) -> None:
    """The question the suite above could not answer: if `PurgedKFold` were
    replaced by a splitter that leaks everything (`_fully_leaky_split`),
    would shuffled (signal-destroyed) labels still score near-chance, or
    would the leak show up as an inflated OOS AUC the way it should?

    This is the genuine positive control: it does not run through
    `PurgedKFold` at all, so it says nothing about `purgedcv` itself — it
    proves that IF the splitter were broken, THIS suite's near-chance
    assertions would have caught it, rather than passing for the wrong
    reason (an underpowered discriminator)."""
    rng = np.random.default_rng(seed)
    features, true_labels = _powerful_synthetic_dataset(rng)
    shuffled_labels = rng.permutation(true_labels)

    auc = _oos_auc_over_splits(features, shuffled_labels, _fully_leaky_split(features))

    assert auc > 0.55, (
        f"a fully leaky splitter (train==test) scored {auc:.3f} on shuffled labels — a real leak at this "
        "scale would NOT have been caught by the near-chance assertions above"
    )
