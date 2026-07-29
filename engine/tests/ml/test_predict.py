"""`te.ml.predict.ShadowMLHook` — the concrete `MLHook` wiring `MetaModel` +
`FeatureSpec` + `BarStore` + `MaturityGate` behind the narrow interface
`te.engine.cycle` depends on."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

from te.data.barstore import BAR_COLUMNS, BarStore
from te.domain.clock import IST
from te.ml.calibrate import fit_platt_calibrator
from te.ml.featurespec import SECONDARY_V1
from te.ml.gates import MaturityGate, Stage, ml_predictions
from te.ml.model import MetaModel
from te.ml.predict import ShadowMLHook
from te.persistence.db import make_engine, make_session_factory

INSTRUMENT = "NIFTY30JUN2626500CE"
VIX_SYMBOL = "INDIAVIX"


def _bar(symbol: str, event_ts: dt.datetime, *, c: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "exchange": "NFO",
        "event_ts": event_ts,
        "interval": "1d",
        "o": c,
        "h": c,
        "l": c,
        "c": c,
        "v": 1000,
        "oi": 0,
        "ingested_at": event_ts,
        "source": "test",
    }


@pytest.fixture
def store(tmp_path: Path) -> BarStore:
    s = BarStore(tmp_path / "bars")
    base = dt.datetime(2026, 6, 1, tzinfo=IST)
    rows = [_bar(VIX_SYMBOL, base + dt.timedelta(days=i), c=12.0 + (i % 3)) for i in range(10)]
    rows += [_bar(INSTRUMENT, base + dt.timedelta(days=i), c=30.0 + i * 0.1) for i in range(10)]
    s.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))
    return s


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'predict_test.db'}")
    return make_session_factory(engine)


@pytest.fixture
def model() -> MetaModel:
    # `india_vix_term_slope` is legitimately NaN whenever the caller doesn't
    # supply near/next VIX futures symbols (see `te.ml.dataset`'s
    # docstring) — a real training pipeline fits its imputer INSIDE the
    # training fold, per the plan; mirror that here with a small Pipeline
    # rather than a bare `LogisticRegression` (which rejects NaN inputs).
    rng = np.random.default_rng(0)
    x = pd.DataFrame({col: rng.normal(size=100) for col in SECONDARY_V1.columns})
    y = (x["dte"] > 0).astype(int).to_numpy()
    base = make_pipeline(SimpleImputer(strategy="mean"), LogisticRegression()).fit(x.to_numpy(), y)
    calibrated = fit_platt_calibrator(base, x.to_numpy(), y, n_splits=3)
    return MetaModel(calibrated=calibrated, feature_spec=SECONDARY_V1)


def test_shadow_hook_evaluates_predicts_and_logs_without_displaying(
    store: BarStore,
    session_factory,
    model: MetaModel,  # noqa: ANN001
) -> None:
    gate = MaturityGate(session_factory)
    assert gate.current_stage() is Stage.SHADOW

    hook = ShadowMLHook(model=model, spec=SECONDARY_V1, store=store, gate=gate, session_factory=session_factory)
    as_of = dt.datetime(2026, 6, 9, 10, 0, tzinfo=IST)
    influence = hook.evaluate(instrument=INSTRUMENT, as_of=as_of, cycle_id=1)

    assert influence.size_multiplier == 1
    assert influence.veto is False
    assert influence.displayed_verdict is None

    with session_factory() as session:
        rows = session.execute(sa.select(ml_predictions)).all()
    assert len(rows) == 1
    assert rows[0].displayed is False
    assert rows[0].stage == "shadow"
    assert rows[0].feature_spec_name == "secondary"
    assert rows[0].feature_spec_version == SECONDARY_V1.version


def test_evaluate_reads_the_maturity_stage_exactly_once(
    store: BarStore,
    session_factory,  # noqa: ANN001
    model: MetaModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`influence()` and the prediction log both need the stage; it must be
    read from `ml_maturity_state` once per prediction, not re-queried."""
    gate = MaturityGate(session_factory)
    hook = ShadowMLHook(model=model, spec=SECONDARY_V1, store=store, gate=gate, session_factory=session_factory)

    calls = 0
    real = gate.current_stage

    def _counting() -> Stage:
        nonlocal calls
        calls += 1
        return real()

    monkeypatch.setattr(gate, "current_stage", _counting)

    hook.evaluate(instrument=INSTRUMENT, as_of=dt.datetime(2026, 6, 9, 10, 0, tzinfo=IST), cycle_id=1)
    assert calls == 1, f"maturity stage re-read {calls} times for one prediction"

    # The logged stage still matches the gate's actual stage.
    with session_factory() as session:
        rows = session.execute(sa.select(ml_predictions)).all()
    assert rows[0].stage == real().value


def test_influence_with_an_explicit_stage_matches_reading_it_from_the_db(
    session_factory,  # noqa: ANN001
) -> None:
    """Threading the stage through must not change the gating decision."""
    from te.persistence.models import Base

    with session_factory() as session:
        Base.metadata.create_all(session.get_bind())  # `gating` consults engine_state
    gate = MaturityGate(session_factory)
    for stage in Stage:
        gate.set_stage(stage, actor="test")
        for p in (0.1, 0.5, 0.9):
            assert gate.influence(p, stage=stage) == gate.influence(p)
