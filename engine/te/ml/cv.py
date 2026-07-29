"""Thin adapter around `purgedcv` (PyPI, MIT, pinned `==0.1.2` — alpha
software per the plan's R2: "an independent leakage test verifies every
split; `te/ml/cv.py` stays a thin adapter so swapping to `skfolio` is one
file"). Do not add logic here beyond embargo-percentage plumbing — the
plan is explicit that `purgedcv`'s own purge/embargo correctness must NOT
be trusted blindly; see `tests/leakage/test_no_train_test_overlap.py` for
an INDEPENDENT verification that never calls back into `purgedcv`'s own
internals.

`PurgedKFold`/`CombinatorialPurgedCV` here are the same classes `purgedcv`
exports, just defaulting `embargo` to 1% of the label time series' full
span when the caller doesn't supply one explicitly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from purgedcv import CombinatorialPurgedCV as _CombinatorialPurgedCV
from purgedcv import PurgedKFold as _PurgedKFold
from purgedcv import apply_embargo as _apply_embargo
from purgedcv import purge as _purge
from purgedcv._time import HorizonLike  # noqa: PLC2701 (this module IS purgedcv's adapter boundary)

DEFAULT_EMBARGO_PCT = 0.01

IntArray = np.ndarray[tuple[int, ...], np.dtype[np.int_]]


def purge_and_embargo(
    train_idx: IntArray,
    test_idx: IntArray,
    *,
    prediction_times: pd.Series,
    evaluation_times: pd.Series,
    gap: pd.Timedelta,
) -> IntArray:
    """Purge + embargo a SINGLE, caller-supplied `(train_idx, test_idx)`
    pair — the primitive `PurgedKFold`/`CombinatorialPurgedCV` apply
    internally, exposed here so a split this module does not itself generate
    (notably `te.ml.train`'s OUTER walk-forward loop) can be cleaned with
    the same, already-tested purgedcv machinery rather than hand-rolled
    logic.

    `gap` is used for BOTH halves, which is the point of taking one value:

    - as purgedcv's `purge_horizon`, symmetrically padding every test row's
      label window to `[prediction_time - gap, evaluation_time + gap)` before
      dropping any training row whose own label window overlaps it, and
    - as the post-test `embargo`, dropping training rows whose prediction
      time falls in `[test_evaluation_time, test_evaluation_time + gap]`.

    Callers should pass the label horizon (`max_hold`) as `gap`, NOT the
    span-proportional `DEFAULT_EMBARGO_PCT` — the leakage being blocked is
    label-lifetime-shaped, so the gap must be measured in the same units.

    Note the embargo half is a no-op for a strictly expanding walk-forward
    window (all training rows precede the test block, and embargo is
    deliberately asymmetric/forward-looking); it is applied anyway so this
    helper stays correct for callers whose training rows straddle the test
    block."""
    purged = _purge(train_idx, test_idx, prediction_times, evaluation_times, gap)
    embargoed: IntArray = _apply_embargo(purged, test_idx, prediction_times, evaluation_times, gap)
    return embargoed


def _default_embargo(evaluation_times: pd.Series) -> pd.Timedelta:
    """1% of the full span between the earliest and latest evaluation
    time — the plan's stated default embargo."""
    times = pd.to_datetime(evaluation_times)
    span = times.max() - times.min()
    return span * DEFAULT_EMBARGO_PCT


class PurgedKFold(_PurgedKFold):
    """`purgedcv.PurgedKFold` with `embargo` defaulted to 1% of the full
    `evaluation_times` span when not supplied."""

    def __init__(
        self,
        n_splits: int,
        *,
        prediction_times: pd.Series,
        evaluation_times: pd.Series,
        purge_horizon: HorizonLike | None = None,
        embargo: HorizonLike | None = None,
    ) -> None:
        resolved_embargo = embargo if embargo is not None else _default_embargo(evaluation_times)
        super().__init__(
            n_splits,
            prediction_times=prediction_times,
            evaluation_times=evaluation_times,
            purge_horizon=purge_horizon,
            embargo=resolved_embargo,
        )


class CombinatorialPurgedCV(_CombinatorialPurgedCV):
    """`purgedcv.CombinatorialPurgedCV` with `embargo` defaulted to 1% of
    the full `evaluation_times` span when not supplied."""

    def __init__(
        self,
        n_splits: int,
        n_test_groups: int,
        *,
        prediction_times: pd.Series,
        evaluation_times: pd.Series,
        purge_horizon: HorizonLike | None = None,
        embargo: HorizonLike | None = None,
    ) -> None:
        resolved_embargo = embargo if embargo is not None else _default_embargo(evaluation_times)
        super().__init__(
            n_splits,
            n_test_groups,
            prediction_times=prediction_times,
            evaluation_times=evaluation_times,
            purge_horizon=purge_horizon,
            embargo=resolved_embargo,
        )
