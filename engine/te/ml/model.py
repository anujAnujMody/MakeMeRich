"""`MetaModel` — the trained + calibrated meta-labeling model artifact.
`predict_proba()` is the only thing this class exposes; it is the sole
producer of the raw probability `p` that everything downstream of
`te.ml.gates.MaturityGate.influence(p)` must never see directly (`cycle.py`
imports neither this class nor calls `predict_proba` — only an `MLHook`
implementation in `te.ml.predict` does).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV

from te.ml.featurespec import FeatureSpec


@dataclass(frozen=True)
class MetaModel:
    """Wraps an already-fit, already-calibrated
    `sklearn.calibration.CalibratedClassifierCV` (Platt/sigmoid only — see
    `te.ml.calibrate`) plus the exact `FeatureSpec` it was trained against,
    so a feature vector is always sliced/ordered consistently with training."""

    calibrated: CalibratedClassifierCV
    feature_spec: FeatureSpec

    def predict_proba(self, features: pd.Series) -> float:
        """Returns P(label=1) — "will this ORB firing hit its net target
        before its net stop?" — for one feature row, built by
        `te.ml.dataset.build_training_set`. Raises `KeyError` if `features`
        is missing any of `self.feature_spec.columns` (fail loudly rather
        than silently predicting off a misaligned vector)."""
        ordered = features[list(self.feature_spec.columns)].to_numpy(dtype=float).reshape(1, -1)
        proba: np.ndarray[tuple[int, ...], np.dtype[np.float64]] = self.calibrated.predict_proba(ordered)
        return float(proba[0, 1])
