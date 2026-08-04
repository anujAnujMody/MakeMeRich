"""`te.ml.nightly` — the scheduled training job and the shadow hook it feeds.

Before this module existed the ML layer could not learn: nothing trained on
a schedule, `model_registry` was empty, and so `te.engine.scheduler` passed
`ml_hook=None` into every cycle. The tests here pin the three properties
that make wiring it in SAFE, because each one is the difference between
"the system starts learning" and "the system starts trading on an untested
model":

1. It registers, it never promotes.
2. It declines rather than trains on a sample too small to mean anything.
3. Its absence is inert — no model registered means `ml_hook=None`, which is
   exactly the behaviour that shipped before.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
from sklearn.linear_model import LogisticRegression
from structlog.testing import capture_logs

from te.data.barstore import BarStore
from te.ml.calibrate import fit_platt_calibrator
from te.ml.featurespec import SECONDARY_V1
from te.ml.gates import Stage, ml_maturity_state, model_promotions
from te.ml.gates import metadata as gates_metadata
from te.ml.model import MetaModel
from te.ml.nightly import (
    MIN_SAMPLES_TO_TRAIN,
    MODEL_NAME,
    ShadowHookProvider,
    build_shadow_hook,
    run_nightly_training,
    train_and_register,
)
from te.ml.registry import get_latest_model_record, register_model
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base


@pytest.fixture
def engine(tmp_path: Path):  # noqa: ANN201
    return make_engine(f"sqlite:///{tmp_path / 'nightly_test.db'}")


@pytest.fixture
def session_factory(engine):  # noqa: ANN001, ANN201
    # The gate/prediction tables, created up front so the assertions below
    # can read them directly. In production `MaturityGate.__init__` and
    # `register_model` each create what they need on first use.
    gates_metadata.create_all(engine, checkfirst=True)
    # `cycle_evaluations` and friends: labelling reads them, and an empty
    # table (no firings yet) is the state under test, not a missing one.
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def store(tmp_path: Path) -> BarStore:
    return BarStore(tmp_path / "bars")


@pytest.fixture
def model() -> MetaModel:
    rng = np.random.default_rng(0)
    n = 100
    x = pd.DataFrame({col: rng.normal(size=n) for col in SECONDARY_V1.columns})
    y = (x["dte"] > 0).astype(int).to_numpy()
    base = LogisticRegression().fit(x.to_numpy(), y)
    calibrated = fit_platt_calibrator(base, x.to_numpy(), y, n_splits=3)
    return MetaModel(calibrated=calibrated, feature_spec=SECONDARY_V1)


def _register(session_factory, model: MetaModel, *, version: int) -> int:  # noqa: ANN001
    return register_model(
        session_factory,
        model=model,
        name=MODEL_NAME,
        version=version,
        gbm_library="xgboost",
        calibration_slope=None,
        dsr=0.1,
        pbo=0.9,
        n_trials_at_training=5,
        n_labeled_samples=250,
    )


def test_no_registered_model_means_no_hook(session_factory, store: BarStore) -> None:  # noqa: ANN001
    """The state on the day this shipped. `None` flows through to
    `run_entry_cycle(ml_hook=None)`, which is byte-for-byte the behaviour
    that ran before — so turning shadow learning on cannot change trading
    until a model genuinely exists."""
    assert build_shadow_hook(session_factory, store) is None
    assert ShadowHookProvider(session_factory, store)() is None


def test_a_registered_model_becomes_a_hook_carrying_its_own_spec(  # noqa: ANN001
    session_factory, store: BarStore, model: MetaModel
) -> None:
    """The spec must come off the ARTIFACT, not from whatever
    `SECONDARY_V1` happens to be at import time. A model trained against an
    older spec that were then fed today's feature vocabulary would predict
    off a misaligned row — silently, since both are just floats."""
    _register(session_factory, model, version=1)

    hook = build_shadow_hook(session_factory, store)

    assert hook is not None
    assert hook.spec == model.feature_spec
    assert hook.model is not model, "returned the in-memory model rather than the persisted artifact"


def test_the_provider_reloads_only_when_a_newer_model_is_registered(  # noqa: ANN001
    session_factory, store: BarStore, model: MetaModel
) -> None:
    """The paper cycle runs every minute. Unpickling the artifact on each
    one is waste; never reloading means last night's model is ignored until
    someone restarts the engine — which for a nightly-learning system is the
    worse bug of the two."""
    _register(session_factory, model, version=1)
    provider = ShadowHookProvider(session_factory, store)

    first = provider()
    again = provider()
    assert first is again, "reloaded the artifact with no new model registered"

    _register(session_factory, model, version=2)
    after = provider()
    assert after is not first, "did not pick up the newly registered model"


def test_training_declines_on_a_sample_too_small_to_mean_anything(  # noqa: ANN001
    session_factory, engine, store: BarStore
) -> None:
    """An empty DB is the state of a young paper run, not a fault. Per
    `honest-metrics` a model fitted on a handful of rows is a fabricated
    number waiting to be displayed, so the job returns `None` — and must not
    raise, or the scheduler would report a healthy system as failing."""
    result = train_and_register(session_factory, engine, store, _cost_model(), instruments=("NIFTY",))

    assert result is None
    assert get_latest_model_record(session_factory, MODEL_NAME) is None, "registered a model it never trained"


def test_the_nightly_job_never_promotes(session_factory, engine, store: BarStore, model: MetaModel) -> None:  # noqa: ANN001
    """The whole safety argument for training automatically. Registering
    writes an artifact; promoting grants it authority over real orders. This
    job does the first and must never do the second — `scripts/promote_model.py`
    is the only thing allowed to, and only with a human behind it."""
    _register(session_factory, model, version=1)

    run_nightly_training(session_factory, engine, store, _cost_model(), instruments=("NIFTY",))

    with session_factory() as session:
        stage_rows = session.execute(sa.select(ml_maturity_state.c.stage)).scalars().all()
        promotions = session.execute(sa.select(sa.func.count()).select_from(model_promotions)).scalar_one()

    assert promotions == 0, "the nightly job wrote a promotion record"
    assert Stage.ADVISORY.value not in stage_rows
    assert Stage.GATING.value not in stage_rows


def test_an_instrument_with_no_atm_snapshot_is_reported_not_silently_dropped(  # noqa: ANN001
    session_factory, engine, store: BarStore
) -> None:
    """Its premium barriers cannot be restated in index points, so it
    genuinely cannot be labelled — but a smaller-than-expected sample must
    never be the only evidence of that."""
    with capture_logs() as logs:
        run_nightly_training(session_factory, engine, store, _cost_model(), instruments=("RELIANCE",))

    warned = [entry for entry in logs if "no ATM snapshot" in entry["event"]]
    assert warned, f"the unlabellable instrument was dropped silently: {logs}"
    assert warned[0]["instruments"] == ("RELIANCE",)


def test_training_failures_never_escape_into_the_scheduler(session_factory, engine, store: BarStore) -> None:  # noqa: ANN001
    """A layer that is structurally forbidden from changing a decision must
    also be unable to stop one. `run_nightly_training` is what APScheduler
    calls; anything it lets through becomes a job error on a system that is
    trading correctly."""

    class Exploding:
        def __getattr__(self, name: str) -> object:
            raise RuntimeError("boom")

    run_nightly_training(session_factory, engine, store, Exploding(), instruments=("NIFTY",))  # type: ignore[arg-type]


def test_the_sample_floor_is_at_least_the_honest_metrics_floor() -> None:
    """`honest-metrics` puts the floor for a displayed Sharpe/profit-factor
    at n=100. A classifier trained to influence trades cannot have a lower
    bar than a number we merely print."""
    assert MIN_SAMPLES_TO_TRAIN >= 100


def _cost_model():  # noqa: ANN202
    from te.data.charges_loader import load_charge_rate_table
    from te.domain.costs import CostModel
    from te.settings import Settings

    return CostModel(load_charge_rate_table(Settings().charges_path))
