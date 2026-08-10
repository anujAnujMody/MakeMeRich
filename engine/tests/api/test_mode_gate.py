"""`POST /api/mode {mode:'live'}` must go through `LiveUnlockGate.check()`
first — 409 with the real failing conditions if unmet, otherwise the
existing mode-switch logic. Switching TO dry-run is never gated. This suite
runs against an ISOLATED sqlite file DB (monkeypatched onto
`te.api.routers.mode.session_factory`) rather than the shared in-memory app
singleton (`te.api.db.session_factory`) — inserting live-gate fixture data
(trades, model_registry rows, ...) into the process-wide singleton would
leak into `tests/contract`'s zero-fabricated-numbers contract test, which
shares that same singleton engine for the whole pytest session."""

from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import te.api.routers.mode as mode_router
from te.domain.money import Paise
from te.engine.state import set_params_frozen_at
from te.ml import registry as ml_registry
from te.ml.gates import MaturityGate, Stage
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, CycleRow, TradeRow
from te.risk.live_gate import MIN_SLIPPAGE_OBSERVATIONS
from te.risk.monitors import SlippageMonitor

STRATEGY = "orb"
INSTRUMENT = "NIFTY"
NOW = dt.datetime(2026, 7, 29, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def isolated_client(tmp_path: Path, monkeypatch):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'mode_gate_api_test.db'}")
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    monkeypatch.setattr(mode_router, "session_factory", sf)

    from te.api.main import app

    return TestClient(app), sf


def _satisfy_all_conditions(sf) -> None:  # noqa: ANN001
    with sf() as session:
        engine = session.get_bind()
        ml_registry.metadata.create_all(engine, checkfirst=True)
        session.execute(
            ml_registry.model_registry.insert().values(
                name=STRATEGY,
                version=1,
                feature_spec_name="secondary",
                feature_spec_version=1,
                artifact_blob=b"",
                gbm_library="catboost",
                calibration_method="sigmoid",
                calibration_slope=1.0,
                dsr=0.97,
                pbo=0.02,
                n_trials_at_training=50,
                n_labeled_samples=250,
                created_at=NOW,
                notes="",
            )
        )
        session.commit()

    frozen_at = NOW - dt.timedelta(days=200)
    with sf() as session:
        set_params_frozen_at(session, frozen_at)
        session.commit()
    for i in range(90):
        closed_at = frozen_at + dt.timedelta(days=i + 1)
        with sf() as session:
            session.add(
                TradeRow(
                    client_order_id=f"co-{i}",
                    symbol="NIFTY30JUL2624500CE",
                    exchange="NFO",
                    strategy=STRATEGY,
                    direction="long",
                    lots=1,
                    lot_size=65,
                    entry_premium_paise=10_000,
                    exit_premium_paise=10_500,
                    gross_pnl_paise=500,
                    costs_paise=0,
                    net_pnl_paise=500,
                    exit_reason="target",
                    mode="paper",
                    opened_at=closed_at - dt.timedelta(minutes=30),
                    closed_at=closed_at,
                )
            )
            session.commit()

    with sf() as session:
        # A REAL clean sample: enough observations to clear the gate's
        # `MIN_SLIPPAGE_OBSERVATIONS` floor, and scattered either side of the
        # benchmark so the spread is non-zero.
        #
        # This used to record 10 observations of `expected == actual`, which
        # is the signature of SIMULATED execution — `SimulatedBroker` fills
        # every order at exactly its limit price. That sample drives stdev to
        # 0, z-score to None and `breached` to False, so it "satisfied" the
        # slippage condition while carrying no evidence at all about
        # execution quality. See `te.risk.live_gate`.
        rng = random.Random(11)
        monitor = SlippageMonitor(session, instrument=INSTRUMENT)
        for i in range(MIN_SLIPPAGE_OBSERVATIONS + 10):
            monitor.observe(Paise(10_000), Paise(10_000 + round(rng.gauss(0, 5))), ctx=f"clean-{i}")
        session.commit()

    gate = MaturityGate(sf)
    gate.set_stage(Stage.GATING, actor="test")
    promoted_at = NOW - dt.timedelta(days=100)
    import sqlalchemy as sa

    from te.ml.gates import model_promotions

    with sf() as session:
        session.execute(
            sa.update(model_promotions)
            .where(model_promotions.c.to_stage == Stage.GATING.value)
            .values(ts=promoted_at)
        )
        session.commit()
    for i in range(60):
        with sf() as session:
            session.add(CycleRow(ts=promoted_at + dt.timedelta(days=i + 1), mode="paper"))
            session.commit()


def test_post_mode_live_returns_409_when_gate_fails(isolated_client) -> None:  # noqa: ANN001
    client, _sf = isolated_client
    response = client.post("/api/mode", json={"mode": "live"})

    assert response.status_code == 409
    body = response.json()
    failing = body["detail"]["failingConditions"]
    assert len(failing) >= 3
    joined = " | ".join(failing)
    assert "DSR" in joined
    assert "params_frozen_at" in joined

    # mode was NOT changed
    mode_response = client.get("/api/mode")
    assert mode_response.json()["mode"] == "dry-run"


def test_post_mode_live_succeeds_when_gate_passes(isolated_client) -> None:  # noqa: ANN001
    client, sf = isolated_client
    _satisfy_all_conditions(sf)

    response = client.post("/api/mode", json={"mode": "live"})

    assert response.status_code == 200
    assert response.json()["mode"] == "live"

    mode_response = client.get("/api/mode")
    assert mode_response.json()["mode"] == "live"


def test_post_mode_dry_run_never_gated(isolated_client) -> None:  # noqa: ANN001
    client, _sf = isolated_client
    response = client.post("/api/mode", json={"mode": "dry-run"})

    assert response.status_code == 200
    assert response.json()["mode"] == "dry-run"
