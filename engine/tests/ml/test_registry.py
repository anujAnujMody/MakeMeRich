"""`te.ml.registry` — model artifact + provenance persistence."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression

from te.ml.calibrate import fit_platt_calibrator
from te.ml.featurespec import SECONDARY_V1
from te.ml.model import MetaModel
from te.ml.registry import get_latest_model_record, load_model_artifact, register_model
from te.persistence.db import make_engine, make_session_factory


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'registry_test.db'}")
    return make_session_factory(engine)


@pytest.fixture
def model() -> MetaModel:
    rng = np.random.default_rng(0)
    n = 100
    x = pd.DataFrame({col: rng.normal(size=n) for col in SECONDARY_V1.columns})
    y = (x["dte"] > 0).astype(int).to_numpy()
    base = LogisticRegression().fit(x.to_numpy(), y)
    calibrated = fit_platt_calibrator(base, x.to_numpy(), y, n_splits=3)
    return MetaModel(calibrated=calibrated, feature_spec=SECONDARY_V1)


def test_register_and_retrieve_latest_model(session_factory, model: MetaModel) -> None:  # noqa: ANN001
    record_id = register_model(
        session_factory,
        model=model,
        name="orb-secondary",
        version=1,
        gbm_library="xgboost",
        calibration_slope=1.05,
        dsr=0.4,
        pbo=0.3,
        n_trials_at_training=12,
        n_labeled_samples=250,
    )
    assert record_id > 0

    record = get_latest_model_record(session_factory, "orb-secondary")
    assert record is not None
    assert record.id == record_id
    assert record.feature_spec_name == "secondary"
    assert record.feature_spec_version == SECONDARY_V1.version
    assert record.gbm_library == "xgboost"
    assert record.dsr == 0.4
    assert record.n_labeled_samples == 250


def test_get_latest_model_record_returns_none_when_absent(session_factory) -> None:  # noqa: ANN001
    assert get_latest_model_record(session_factory, "does-not-exist") is None


def test_load_model_artifact_round_trips(session_factory, model: MetaModel) -> None:  # noqa: ANN001
    record_id = register_model(
        session_factory,
        model=model,
        name="orb-secondary",
        version=1,
        gbm_library="xgboost",
        calibration_slope=None,
        dsr=0.4,
        pbo=0.3,
        n_trials_at_training=12,
        n_labeled_samples=250,
    )
    loaded = load_model_artifact(session_factory, record_id)
    assert loaded.feature_spec == SECONDARY_V1

    features = pd.Series({col: 0.5 for col in SECONDARY_V1.columns})
    assert loaded.predict_proba(features) == pytest.approx(model.predict_proba(features))


def test_latest_returns_the_most_recently_registered_row(session_factory, model: MetaModel) -> None:  # noqa: ANN001
    register_model(
        session_factory,
        model=model,
        name="orb-secondary",
        version=1,
        gbm_library="xgboost",
        calibration_slope=None,
        dsr=0.1,
        pbo=0.5,
        n_trials_at_training=1,
        n_labeled_samples=200,
    )
    second_id = register_model(
        session_factory,
        model=model,
        name="orb-secondary",
        version=2,
        gbm_library="xgboost",
        calibration_slope=None,
        dsr=0.5,
        pbo=0.2,
        n_trials_at_training=2,
        n_labeled_samples=210,
    )
    record = get_latest_model_record(session_factory, "orb-secondary")
    assert record is not None
    assert record.id == second_id
    assert record.version == 2
