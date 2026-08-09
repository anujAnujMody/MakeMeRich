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

import datetime as dt
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
from sklearn.linear_model import LogisticRegression
from structlog.testing import capture_logs

from te.data.barstore import BAR_COLUMNS, BarStore
from te.domain.clock import IST
from te.domain.geometry import RupeeRiskGeometry
from te.domain.money import Paise
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

#: The live `Settings.paper_cycle_max_loss_per_trade_paise` (Rs 700) /
#: `paper_cycle_target_risk_multiple` (10) geometry, so these tests exercise
#: the same shape of geometry the real caller (`run_nightly_training_job` in
#: `te.engine.scheduler`) supplies — never the old hardcoded 20%/20%.
_LIVE_GEOMETRY = RupeeRiskGeometry(max_loss_paise=Paise(70_000), target_multiple=Decimal(10))
_LIVE_MAX_LOTS = 1


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
    result = train_and_register(
        session_factory,
        engine,
        store,
        _cost_model(),
        geometry=_LIVE_GEOMETRY,
        max_lots=_LIVE_MAX_LOTS,
        instruments=("NIFTY",),
    )

    assert result is None
    assert get_latest_model_record(session_factory, MODEL_NAME) is None, "registered a model it never trained"


def test_the_nightly_job_never_promotes(session_factory, engine, store: BarStore, model: MetaModel) -> None:  # noqa: ANN001
    """The whole safety argument for training automatically. Registering
    writes an artifact; promoting grants it authority over real orders. This
    job does the first and must never do the second — `scripts/promote_model.py`
    is the only thing allowed to, and only with a human behind it."""
    _register(session_factory, model, version=1)

    run_nightly_training(
        session_factory,
        engine,
        store,
        _cost_model(),
        geometry=_LIVE_GEOMETRY,
        max_lots=_LIVE_MAX_LOTS,
        instruments=("NIFTY",),
    )

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
        run_nightly_training(
            session_factory,
            engine,
            store,
            _cost_model(),
            geometry=_LIVE_GEOMETRY,
            max_lots=_LIVE_MAX_LOTS,
            instruments=("RELIANCE",),
        )

    warned = [entry for entry in logs if "no ATM snapshot" in entry["event"]]
    assert warned, f"the unlabellable instrument was dropped silently: {logs}"
    assert warned[0]["instruments"] == ("RELIANCE",)


def _seed_traded_evaluation(session_factory, *, instrument: str, entry_ts: dt.datetime, evaluation_id: str) -> None:  # noqa: ANN001
    """One `verdict == "traded"` evaluation carrying the breakout condition
    `label_firings_from_evaluations` keys off — the minimum `build_labeled_dataset`
    needs to produce a real (non-empty) labelled row."""
    from te.domain.evaluation import ConditionResult, Evaluation
    from te.ml.labeling import _BREAKOUT_CONDITION_LABEL
    from te.persistence.repos.paper_trading import record_cycle, record_evaluation

    with session_factory() as session:
        cycle_id = record_cycle(session, ts=entry_ts, mode="paper")
        record_evaluation(
            session,
            cycle_id=cycle_id,
            evaluation=Evaluation(
                id=evaluation_id,
                timestamp=entry_ts,
                strategy="orb",
                instrument=instrument,
                verdict="traded",
                reason="breakout confirmed",
                conditions=(
                    ConditionResult(
                        label=_BREAKOUT_CONDITION_LABEL,
                        required="close > range high",
                        actual="close=24000.00, range=[23800.00, 23900.00]",
                        passed=True,
                        evaluated=True,
                    ),
                ),
            ),
        )
        session.commit()


def _index_bar(symbol: str, event_ts: dt.datetime, *, h: float, low: float, c: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "exchange": "NFO",
        "event_ts": event_ts,
        "interval": "1m",
        "o": c,
        "h": h,
        "l": low,
        "c": c,
        "v": 1_000,
        "oi": 0,
        "ingested_at": event_ts,
        "source": "test",
    }


def test_build_labeled_dataset_barriers_come_from_the_callers_geometry_not_a_constant(  # noqa: ANN001
    session_factory, store: BarStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`build_labeled_dataset` has no test caller at all in the audit's
    finding — a hardcode of `max_lots=100` (or a fixed
    `PremiumPercentGeometry(20, 20)`) at `nightly.py:118` would compute every
    label at a geometry the engine does not trade, and the whole suite would
    stay green. Seed a real traded evaluation plus index bars, spy on
    `label_firings_from_evaluations` (the function `build_labeled_dataset`
    threads `stop`/`target` into), and assert the distances it actually
    receives equal `barriers(symbol, geometry=..., max_lots=...)` computed
    independently with the SAME caller-supplied geometry/max_lots — i.e. the
    caller's geometry, not a constant, sets the barriers."""
    import te.ml.nightly as nightly_module
    from te.ml.barriers import barriers as real_barriers
    from te.ml.nightly import build_labeled_dataset

    entry_ts = dt.datetime(2026, 6, 1, 9, 30, tzinfo=IST)
    _seed_traded_evaluation(session_factory, instrument="NIFTY", entry_ts=entry_ts, evaluation_id="eval-0")

    # A real, non-trivial price path under the traded instrument so labelling
    # actually runs to completion rather than short-circuiting on empty bars.
    rows = [
        _index_bar("NIFTY", entry_ts + dt.timedelta(minutes=i), h=24_500.0, low=23_500.0, c=24_000.0)
        for i in range(1, 20)
    ]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))

    captured: dict[str, object] = {}
    real_label_firings = nightly_module.label_firings_from_evaluations

    def _spy(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        captured["stop_distance"] = kwargs["stop_distance"]
        captured["target_distance"] = kwargs["target_distance"]
        return real_label_firings(*args, **kwargs)

    monkeypatch.setattr(nightly_module, "label_firings_from_evaluations", _spy)

    frame = build_labeled_dataset(
        session_factory,
        store,
        _cost_model(),
        geometry=_LIVE_GEOMETRY,
        max_lots=_LIVE_MAX_LOTS,
        instruments=("NIFTY",),
    )

    assert len(frame) > 0, "the seeded firing produced no labelled rows — the test setup itself is broken"
    assert captured, "label_firings_from_evaluations was never called"

    expected_stop, expected_target = real_barriers("NIFTY", geometry=_LIVE_GEOMETRY, max_lots=_LIVE_MAX_LOTS)
    assert captured["stop_distance"] == expected_stop
    assert captured["target_distance"] == expected_target

    # A DIFFERENT geometry must produce DIFFERENT barriers reaching the same
    # call — proof this is threaded through, not a coincidentally-matching
    # constant.
    other_geometry = RupeeRiskGeometry(max_loss_paise=Paise(140_000), target_multiple=Decimal(5))
    other_expected_stop, other_expected_target = real_barriers("NIFTY", geometry=other_geometry, max_lots=_LIVE_MAX_LOTS)
    assert (other_expected_stop, other_expected_target) != (expected_stop, expected_target), (
        "test setup picked two geometries that happen to resolve to the same barriers"
    )


def test_geometry_stamp_is_symbol_specific_and_not_1_to_1() -> None:
    """`model_registry.notes` must record what a label was actually computed
    at. Every label used to be silently computed at a hardcoded 20%/20%; the
    stamp now records the real per-symbol percentage under the live
    `RupeeRiskGeometry`, which is neither 20/20 nor the same across symbols
    (Rs 700 is a different fraction of NIFTY's Rs 92.05 premium than of
    BANKNIFTY's Rs 876.75)."""
    from te.ml.nightly import _geometry_stamp

    stamp = _geometry_stamp(("NIFTY", "BANKNIFTY"), geometry=_LIVE_GEOMETRY, max_lots=_LIVE_MAX_LOTS)

    assert "NIFTY=" in stamp
    assert "BANKNIFTY=" in stamp
    nifty_part, banknifty_part = (p for p in stamp.split(", "))
    assert nifty_part != banknifty_part
    assert "-20.00%/+20.00%" not in stamp, "still stamping the old hardcoded 1:1 geometry"


def test_geometry_stamp_names_a_degenerate_symbol_rather_than_omitting_it() -> None:
    """A symbol whose whole premium sits inside the rupee cap at this
    quantity cannot be labelled at all — the stamp must say so explicitly
    rather than silently dropping the symbol from the string."""
    from te.domain.geometry import RupeeRiskGeometry as _RupeeRiskGeometry
    from te.ml.nightly import _geometry_stamp

    # A rupee cap so far ABOVE NIFTY's whole premium (Rs 92.05 at 65 units =
    # Rs 5,983.25 notional) that the resolved stop distance lands at or
    # beyond the entry premium itself — there is no reachable stop.
    degenerate = _RupeeRiskGeometry(max_loss_paise=Paise(1_000_000), target_multiple=Decimal(10))

    stamp = _geometry_stamp(("NIFTY",), geometry=degenerate, max_lots=_LIVE_MAX_LOTS)

    assert stamp == "NIFTY=unlabelled (degenerate)"


def test_training_failures_never_escape_into_the_scheduler(session_factory, engine, store: BarStore) -> None:  # noqa: ANN001
    """A layer that is structurally forbidden from changing a decision must
    also be unable to stop one. `run_nightly_training` is what APScheduler
    calls; anything it lets through becomes a job error on a system that is
    trading correctly.

    Not raising is necessary but not sufficient: a nightly job that dies
    every night and says nothing is a full session of lost training data,
    invisible until someone goes looking. `nightly.py` already imports
    `structlog` and calls `logger.exception("nightly training failed", ...)`
    in this exact except block — assert that event actually lands, at error
    level, rather than only asserting the call didn't raise."""

    class Exploding:
        def __getattr__(self, name: str) -> object:
            raise RuntimeError("boom")

    with capture_logs() as logs:
        run_nightly_training(
            session_factory,
            engine,
            store,
            Exploding(),  # type: ignore[arg-type]
            geometry=_LIVE_GEOMETRY,
            max_lots=_LIVE_MAX_LOTS,
            instruments=("NIFTY",),
        )

    failures = [entry for entry in logs if entry["event"] == "nightly training failed"]
    assert failures, f"the training failure was swallowed with no log entry: {logs}"
    assert failures[0]["log_level"] == "error"


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
