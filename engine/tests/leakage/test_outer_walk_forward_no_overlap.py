"""Independent leakage test for the OUTER walk-forward loop — the sibling of
`test_no_train_test_overlap.py`, which covers only the INNER `PurgedKFold`.

This matters more than the inner check, not less: the outer loop's held-out
blocks are the SOLE source of the reported DSR and PBO
(`te.ml.train.train_meta_model`). An unpurged outer split lets late training
rows — whose triple-barrier labels (`te.ml.labeling`) only resolve up to
`max_hold` AFTER their prediction time — carry information about the very
period the model is then scored on, inflating both metrics.

Like its sibling, the overlap/gap arithmetic here is re-derived from first
principles and never calls back into `purgedcv`'s own internals.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from te.ml.train import outer_walk_forward_splits

N_SAMPLES = 240
LABEL_HORIZON_DAYS = 3  # each sample's label window spans [prediction_time, evaluation_time)


@pytest.fixture
def prediction_times() -> pd.Series:
    return pd.Series(pd.date_range("2026-01-01", periods=N_SAMPLES, freq="D"))


@pytest.fixture
def evaluation_times(prediction_times: pd.Series) -> pd.Series:
    return prediction_times + pd.Timedelta(days=LABEL_HORIZON_DAYS)


def _label_windows_overlap(pred: pd.Series, eval_: pd.Series, i: int, j: int) -> bool:
    """Independent overlap check for two half-open label windows
    [pred[k], eval[k])."""
    a_start, a_end = pred.iloc[i], eval_.iloc[i]
    b_start, b_end = pred.iloc[j], eval_.iloc[j]
    return bool(a_start < b_end and b_start < a_end)


def test_outer_walk_forward_splits_never_leak(prediction_times: pd.Series, evaluation_times: pd.Series) -> None:
    max_hold = (evaluation_times - prediction_times).max()

    splits = outer_walk_forward_splits(
        prediction_times=prediction_times, evaluation_times=evaluation_times, outer_splits=6
    )
    assert len(splits) == 5  # 6 blocks -> block 0 seeds the first train set, 5 evaluated OOS folds

    for train_idx, test_idx in splits:
        assert len(train_idx) > 0
        assert len(test_idx) > 0

        # 1. Index sets disjoint.
        assert set(train_idx.tolist()).isdisjoint(set(test_idx.tolist()))

        # 2. No train label window may overlap any test label window — this
        # is what purge is FOR, and it is what the unpurged outer loop
        # violated on every single fold.
        for i in train_idx.tolist():
            for j in test_idx.tolist():
                assert not _label_windows_overlap(prediction_times, evaluation_times, i, j), (
                    f"train row {i} label window overlaps test row {j} label window — outer purge failed"
                )

        # 3. Embargo: every kept train row's label must have fully resolved
        # at least `max_hold` BEFORE the test block's first prediction time.
        first_test_pred = prediction_times.iloc[test_idx].min()
        for i in train_idx.tolist():
            assert evaluation_times.iloc[i] + max_hold <= first_test_pred, (
                f"train row {i} (evaluation_time={evaluation_times.iloc[i]}) sits inside the "
                f"{max_hold} embargo before the test block starting {first_test_pred} — outer embargo failed"
            )


def test_outer_walk_forward_actually_drops_rows_adjacent_to_the_test_block(
    prediction_times: pd.Series, evaluation_times: pd.Series
) -> None:
    """Guards against a vacuous version of the test above: the purge must
    really remove the boundary rows, not merely be trivially satisfied."""
    splits = outer_walk_forward_splits(
        prediction_times=prediction_times, evaluation_times=evaluation_times, outer_splits=6
    )
    for train_idx, test_idx in splits:
        unpurged_end = int(test_idx.min())
        assert len(train_idx) < unpurged_end, (
            "no rows were purged from the outer training block — the boundary rows whose labels "
            "resolve into the test window are still present"
        )


def test_outer_walk_forward_is_expanding_and_strictly_before_the_test_block(
    prediction_times: pd.Series, evaluation_times: pd.Series
) -> None:
    splits = outer_walk_forward_splits(
        prediction_times=prediction_times, evaluation_times=evaluation_times, outer_splits=6
    )
    previous_test_end = -1
    for train_idx, test_idx in splits:
        # Training rows always precede the test block positionally (walk-forward).
        assert int(train_idx.max()) < int(test_idx.min())
        # Test blocks march forward in time and never revisit an earlier one.
        assert int(test_idx.min()) > previous_test_end
        previous_test_end = int(test_idx.max())


def test_outer_walk_forward_skips_folds_left_with_no_training_rows() -> None:
    """When the label horizon is so long relative to the block size that
    purging empties a training block, that fold must be dropped rather than
    fitted on nothing."""
    n = 60
    pred = pd.Series(pd.date_range("2026-01-01", periods=n, freq="D"))
    # Horizon spans well over a whole block, so early folds purge to empty.
    eval_ = pred + pd.Timedelta(days=40)

    splits = outer_walk_forward_splits(prediction_times=pred, evaluation_times=eval_, outer_splits=6)
    for train_idx, test_idx in splits:
        assert len(train_idx) > 0
        assert len(test_idx) > 0
        assert np.max(train_idx) < np.min(test_idx)
