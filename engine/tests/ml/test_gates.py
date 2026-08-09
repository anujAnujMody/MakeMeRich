"""`te.ml.gates.MaturityGate` — the only place ML may touch a decision.
Direct unit coverage of `influence()` per stage; the cross-module proof that
`te.engine.cycle` is unaffected lives in
`tests/engine/test_cycle_ml_gate.py`."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from te.domain.clock import IST
from te.ml.gates import (
    MaturityGate,
    MLInfluence,
    Stage,
    _engine_state,
    log_prediction,
    ml_maturity_state,
    ml_predictions,
)
from te.persistence.db import make_engine, make_session_factory


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'gates_test.db'}")
    return make_session_factory(engine)


def _set_stage_directly(session_factory, stage: Stage) -> None:  # noqa: ANN001
    with session_factory() as session:
        engine = session.get_bind()
        from te.ml.gates import metadata

        metadata.create_all(engine, checkfirst=True)
        session.execute(sa.delete(ml_maturity_state))
        session.execute(ml_maturity_state.insert().values(id=1, stage=stage.value, updated_at=dt.datetime.now(dt.UTC)))
        session.commit()


def _set_mode_directly(session_factory, mode: str) -> None:  # noqa: ANN001
    with session_factory() as session:
        engine = session.get_bind()
        _engine_state.metadata.create_all(engine, checkfirst=True)
        session.execute(sa.delete(_engine_state).where(_engine_state.c.key == "mode"))
        session.execute(_engine_state.insert().values(key="mode", value=mode, updated_at=dt.datetime.now(dt.UTC)))
        session.commit()


def test_default_stage_is_shadow_when_no_row_exists(session_factory) -> None:  # noqa: ANN001
    gate = MaturityGate(session_factory)
    assert gate.current_stage() is Stage.SHADOW


@pytest.mark.parametrize("stage", [Stage.SHADOW, Stage.ADVISORY])
@pytest.mark.parametrize("p", [0.0, 0.5, 1.0])
def test_influence_below_gating_never_vetoes_or_resizes(session_factory, stage: Stage, p: float) -> None:  # noqa: ANN001
    _set_stage_directly(session_factory, stage)
    gate = MaturityGate(session_factory)
    influence = gate.influence(p)
    assert influence.size_multiplier == 1
    assert influence.veto is False


def test_shadow_prediction_logged_but_not_displayed(session_factory) -> None:  # noqa: ANN001
    _set_stage_directly(session_factory, Stage.SHADOW)
    gate = MaturityGate(session_factory)
    influence = gate.influence(0.9)
    assert influence.displayed_verdict is None

    log_prediction(
        session_factory,
        cycle_id=1,
        instrument="NIFTY30JUN2626500CE",
        feature_spec_name="secondary",
        feature_spec_version=1,
        p=0.9,
        stage=gate.current_stage(),
        displayed=influence.displayed_verdict is not None,
        ts=dt.datetime(2026, 7, 29, 9, 30, tzinfo=IST),
    )
    with session_factory() as session:
        rows = session.execute(sa.select(ml_predictions)).all()
    assert len(rows) == 1
    assert rows[0].displayed is False
    assert rows[0].p == 0.9


def test_advisory_stage_shows_a_verdict_but_still_cannot_veto_or_resize(session_factory) -> None:  # noqa: ANN001
    _set_stage_directly(session_factory, Stage.ADVISORY)
    gate = MaturityGate(session_factory)
    influence = gate.influence(0.9)
    assert influence.displayed_verdict is not None
    assert influence.veto is False
    assert influence.size_multiplier == 1


@pytest.mark.parametrize(("p", "expected_verdict"), [(0.499, "unfavourable"), (0.5, "favourable"), (0.501, "favourable")])
def test_advisory_verdict_boundary_is_favourable_at_exactly_the_threshold(  # noqa: ANN001
    session_factory, p: float, expected_verdict: str
) -> None:
    """`_verdict` uses `p >= _ADVISORY_VERDICT_THRESHOLD` — a boundary flip
    to `p > _ADVISORY_VERDICT_THRESHOLD` would show "unfavourable" at
    exactly `p == 0.5`, and no existing test checks the displayed STRING
    (only that it is non-`None`)."""
    _set_stage_directly(session_factory, Stage.ADVISORY)
    gate = MaturityGate(session_factory)
    influence = gate.influence(p)
    assert influence.displayed_verdict == expected_verdict


def test_gating_stage_can_veto_in_paper_mode(session_factory) -> None:  # noqa: ANN001
    _set_stage_directly(session_factory, Stage.GATING)
    _set_mode_directly(session_factory, "dry-run")
    gate = MaturityGate(session_factory)
    influence = gate.influence(0.0)
    assert influence.veto is True


@pytest.mark.parametrize(("p", "expected_veto"), [(0.499, True), (0.5, False), (0.501, False)])
def test_gating_veto_boundary_is_strictly_less_than_the_threshold(  # noqa: ANN001
    session_factory, p: float, expected_veto: bool
) -> None:
    """The only p values exercised elsewhere at GATING/LIVE_GATING are 0.0
    (veto expected) — a boundary flip from `p < _VETO_THRESHOLD` to
    `p <= _VETO_THRESHOLD` would veto a prediction of exactly 0.5, and no
    existing test would notice. `p == 0.5` is reachable in practice
    (degenerate models, a calibrator on a balanced fold)."""
    _set_stage_directly(session_factory, Stage.GATING)
    _set_mode_directly(session_factory, "dry-run")
    gate = MaturityGate(session_factory)
    influence = gate.influence(p)
    assert influence.veto is expected_veto


def test_gating_stage_cannot_veto_in_live_mode(session_factory) -> None:  # noqa: ANN001
    """Even at GATING stage, if the engine's persisted mode is `live`,
    `influence()` must return `veto=False` regardless of `p` — gating stage
    may only veto in paper mode, per the plan."""
    _set_stage_directly(session_factory, Stage.GATING)
    _set_mode_directly(session_factory, "live")
    gate = MaturityGate(session_factory)
    for p in (0.0, 0.2, 0.5, 0.8, 1.0):
        influence = gate.influence(p)
        assert influence.veto is False


@pytest.mark.parametrize("p", [0.0, 0.1, 0.5, 0.9, 1.0])
def test_size_multiplier_clamped_to_0_5_1_0(session_factory, p: float) -> None:  # noqa: ANN001
    _set_stage_directly(session_factory, Stage.LIVE_GATING)
    gate = MaturityGate(session_factory)
    influence = gate.influence(p)
    assert 0.5 <= influence.size_multiplier <= 1.0


def test_size_multiplier_is_the_probability_itself_in_the_clamp_interior(session_factory) -> None:  # noqa: ANN001
    """A range check (`0.5 <= x <= 1.0`) is satisfied by both the correct
    `size_multiplier = p` mapping AND any monotone distortion of it (e.g.
    `p * 0.5`, still clamped into range at every p in [0, 1]). Pin the
    IDENTITY at an interior point, where the clamp is a no-op and only the
    real mapping can produce the exact value — this multiplies a sized
    position, so a silent distortion here halves real money on every
    live-gated trade."""
    _set_stage_directly(session_factory, Stage.LIVE_GATING)
    gate = MaturityGate(session_factory)
    influence = gate.influence(0.75)
    assert influence.size_multiplier == Decimal("0.75")


def test_live_gating_may_veto_and_resize(session_factory) -> None:  # noqa: ANN001
    _set_stage_directly(session_factory, Stage.LIVE_GATING)
    gate = MaturityGate(session_factory)
    influence = gate.influence(0.0)
    assert influence.veto is True
    assert influence.size_multiplier == pytest.approx(0.5)


@pytest.mark.parametrize(("p", "expected_veto"), [(0.499, True), (0.5, False), (0.501, False)])
def test_live_gating_veto_boundary_is_strictly_less_than_the_threshold(  # noqa: ANN001
    session_factory, p: float, expected_veto: bool
) -> None:
    _set_stage_directly(session_factory, Stage.LIVE_GATING)
    gate = MaturityGate(session_factory)
    influence = gate.influence(p)
    assert influence.veto is expected_veto


def test_set_stage_writes_audit_row_and_updates_current_stage(session_factory) -> None:  # noqa: ANN001
    gate = MaturityGate(session_factory)
    assert gate.current_stage() is Stage.SHADOW
    gate.set_stage(Stage.ADVISORY, actor="test-script", criteria_json='{"n":200}')
    assert gate.current_stage() is Stage.ADVISORY

    with session_factory() as session:
        from te.ml.gates import model_promotions

        rows = session.execute(sa.select(model_promotions)).all()
    assert len(rows) == 1
    assert rows[0].from_stage == "shadow"
    assert rows[0].to_stage == "advisory"
    assert rows[0].actor == "test-script"


def test_mlinfluence_is_frozen_and_typed() -> None:
    influence = MLInfluence(size_multiplier=1, veto=False, displayed_verdict=None)
    with pytest.raises(AttributeError):
        influence.veto = True  # type: ignore[misc]
