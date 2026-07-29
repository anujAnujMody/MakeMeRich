"""`te.ml.model.MetaModel` — thin `predict_proba` wrapper over a fit +
calibrated classifier."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression

from te.ml.calibrate import fit_platt_calibrator
from te.ml.featurespec import SECONDARY_V1
from te.ml.model import MetaModel


@pytest.fixture
def fitted_model() -> MetaModel:
    rng = np.random.default_rng(0)
    n = 200
    x = pd.DataFrame(
        {col: rng.normal(size=n) for col in SECONDARY_V1.columns},
    )
    y = (x["dte"] > 0).astype(int).to_numpy()
    base = LogisticRegression().fit(x.to_numpy(), y)
    calibrated = fit_platt_calibrator(base, x.to_numpy(), y, n_splits=3)
    return MetaModel(calibrated=calibrated, feature_spec=SECONDARY_V1)


def test_predict_proba_returns_a_float_in_0_1(fitted_model: MetaModel) -> None:
    features = pd.Series({col: 1.0 for col in SECONDARY_V1.columns})
    p = fitted_model.predict_proba(features)
    assert isinstance(p, float)
    assert 0.0 <= p <= 1.0


def test_predict_proba_slices_by_feature_spec_columns_regardless_of_input_order(fitted_model: MetaModel) -> None:
    ordered = pd.Series({col: float(i) for i, col in enumerate(SECONDARY_V1.columns)})
    shuffled = ordered.sample(frac=1, random_state=1)
    assert fitted_model.predict_proba(ordered) == pytest.approx(fitted_model.predict_proba(shuffled))


def test_predict_proba_raises_on_missing_column(fitted_model: MetaModel) -> None:
    incomplete = pd.Series({col: 1.0 for col in SECONDARY_V1.columns[:-1]})
    with pytest.raises(KeyError):
        fitted_model.predict_proba(incomplete)
