"""Independent leakage test — per the plan's explicit R2 caveat, `purgedcv`
is alpha (v0.1.2, single maintainer) and its own purge/embargo correctness
must not be trusted blindly. This test re-derives "does this split leak"
from first principles (label-window overlap + embargo window checks written
here, not by calling into `purgedcv`'s own `diagnostics`/internals module)
and runs it against EVERY split `te.ml.cv.PurgedKFold`/
`CombinatorialPurgedCV` produce.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from te.ml.cv import CombinatorialPurgedCV, PurgedKFold

N_SAMPLES = 120
LABEL_HORIZON_DAYS = 3  # each sample's label window spans [prediction_time, evaluation_time)


@pytest.fixture
def prediction_times() -> pd.Series:
    return pd.Series(pd.date_range("2026-01-01", periods=N_SAMPLES, freq="D"))


@pytest.fixture
def evaluation_times(prediction_times: pd.Series) -> pd.Series:
    return prediction_times + pd.Timedelta(days=LABEL_HORIZON_DAYS)


FloatArray = np.ndarray[tuple[int, ...], np.dtype[np.int64]]


def _label_windows_overlap(
    pred: pd.Series, eval_: pd.Series, i: int, j: int
) -> bool:
    """Independent overlap check for two half-open label windows
    [pred[k], eval[k])."""
    a_start, a_end = pred.iloc[i], eval_.iloc[i]
    b_start, b_end = pred.iloc[j], eval_.iloc[j]
    return bool(a_start < b_end and b_start < a_end)


def _assert_no_leakage(
    train_idx: FloatArray,
    test_idx: FloatArray,
    prediction_times: pd.Series,
    evaluation_times: pd.Series,
    embargo: pd.Timedelta,
) -> None:
    train_set = set(train_idx.tolist())
    test_set = set(test_idx.tolist())

    # 1. Train/test index sets must be disjoint.
    assert train_set.isdisjoint(test_set), "train and test index sets overlap"

    for i in train_set:
        for j in test_set:
            # 2. No train label window may overlap a test label window
            # (this is what purge is FOR).
            assert not _label_windows_overlap(prediction_times, evaluation_times, i, j), (
                f"train row {i} label window overlaps test row {j} label window — purge failed"
            )

        # 3. Embargo: no train row's prediction time may fall inside
        # [test_evaluation_time, test_evaluation_time + embargo] for ANY
        # test row's evaluation time.
        pred_i = prediction_times.iloc[i]
        for j in test_set:
            eval_j = evaluation_times.iloc[j]
            in_embargo_window = eval_j <= pred_i <= eval_j + embargo
            assert not in_embargo_window, (
                f"train row {i} (prediction_time={pred_i}) falls inside the embargo window after "
                f"test row {j} (evaluation_time={eval_j}, embargo={embargo}) — embargo failed"
            )


def test_purged_kfold_splits_never_leak(prediction_times: pd.Series, evaluation_times: pd.Series) -> None:
    embargo_pct = 0.01
    span = evaluation_times.max() - evaluation_times.min()
    embargo = span * embargo_pct

    cv = PurgedKFold(
        n_splits=5, prediction_times=prediction_times, evaluation_times=evaluation_times, embargo=embargo
    )
    splits = list(cv.split(np.arange(N_SAMPLES)))
    assert len(splits) == 5

    for train_idx, test_idx in splits:
        assert len(test_idx) > 0
        _assert_no_leakage(train_idx, test_idx, prediction_times, evaluation_times, embargo)


def test_purged_kfold_default_embargo_never_leaks(prediction_times: pd.Series, evaluation_times: pd.Series) -> None:
    """No explicit `embargo=` — `te.ml.cv`'s 1%-of-span default must still
    be leak-free under the same independent check."""
    span = evaluation_times.max() - evaluation_times.min()
    default_embargo = span * 0.01

    cv = PurgedKFold(n_splits=4, prediction_times=prediction_times, evaluation_times=evaluation_times)
    for train_idx, test_idx in cv.split(np.arange(N_SAMPLES)):
        _assert_no_leakage(train_idx, test_idx, prediction_times, evaluation_times, default_embargo)


def test_combinatorial_purged_cv_splits_never_leak(prediction_times: pd.Series, evaluation_times: pd.Series) -> None:
    embargo_pct = 0.01
    span = evaluation_times.max() - evaluation_times.min()
    embargo = span * embargo_pct

    cv = CombinatorialPurgedCV(
        n_splits=6,
        n_test_groups=2,
        prediction_times=prediction_times,
        evaluation_times=evaluation_times,
        embargo=embargo,
    )
    splits = list(cv.split(np.arange(N_SAMPLES)))
    assert len(splits) > 0

    for train_idx, test_idx in splits:
        assert len(test_idx) > 0
        _assert_no_leakage(train_idx, test_idx, prediction_times, evaluation_times, embargo)
