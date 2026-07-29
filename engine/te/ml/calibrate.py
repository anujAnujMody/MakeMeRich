"""Platt/sigmoid calibration ONLY — isotonic calibration is explicitly
BANNED by the plan at this project's sample size (a few thousand rows at
most; isotonic's piecewise-constant fit overfits badly that small).
`tests/ml/test_calibrate.py::test_isotonic_is_never_used` AST/string-greps
every module under `te/ml/` so a future edit can't silently reintroduce it.

Wraps `sklearn.calibration.CalibratedClassifierCV(method="sigmoid", cv=...)`
— the `cv` argument is expected to be a purged splitter from `te.ml.cv`
(`PurgedKFold`/`CombinatorialPurgedCV`), never plain `KFold`, so the
calibration fold itself doesn't leak temporally adjacent, overlapping-label
rows between its own fit/calibrate splits.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
import numpy.typing as npt
from sklearn.calibration import CalibratedClassifierCV


class _CVSplitter(Protocol):
    # `X` mirrors sklearn's own `split(X, y, groups)` argument naming.
    def split(self, X: npt.NDArray[np.float64], y: object = None, groups: object = None) -> object: ...  # noqa: N803

    def get_n_splits(self, X: object = None, y: object = None, groups: object = None) -> int: ...  # noqa: N803


def fit_platt_calibrator(
    estimator: object,
    x: npt.NDArray[np.float64],
    y: npt.NDArray[np.int_],
    *,
    n_splits: int = 5,
    cv: _CVSplitter | int | None = None,
    sample_weight: npt.NDArray[np.float64] | None = None,
) -> CalibratedClassifierCV:
    """Fits a sigmoid (Platt) calibrator on top of an already-trained (or
    unfitted — `CalibratedClassifierCV` handles both) `estimator`. `cv`
    should be a purged splitter (`te.ml.cv.PurgedKFold`/
    `CombinatorialPurgedCV`) in real use; defaults to plain integer K-fold
    (`n_splits`) only for callers (e.g. this module's own unit tests) that
    don't care about temporal leakage.

    `sample_weight` (row-aligned with `x`/`y`) is forwarded to
    `CalibratedClassifierCV.fit`, which supports it natively. The calibrator
    must see the SAME `te.ml.labeling` uniqueness weights the underlying
    model was fitted with — otherwise the calibration map is fitted against a
    differently-weighted view of the data than the scores it is calibrating."""
    resolved_cv = cv if cv is not None else n_splits
    calibrated = CalibratedClassifierCV(estimator, method="sigmoid", cv=resolved_cv)
    calibrated.fit(x, y, sample_weight=sample_weight)
    return calibrated


def calibrate_probabilities(
    calibrated: CalibratedClassifierCV, x: npt.NDArray[np.float64]
) -> npt.NDArray[np.float64]:
    """Returns calibrated P(label=1) for each row of `x`."""
    proba: npt.NDArray[np.float64] = calibrated.predict_proba(x)[:, 1]
    return proba
