"""`te.risk.live_gate.LiveUnlockGate` — the Phase 8 live-money unlock gate.
Checks every condition against REAL persisted data (never fabricated/
assumed-true): strategy DSR>0.95 with honest N, PBO<0.05, >=90 sessions of
net-positive paper P&L after `params_frozen_at`, Tier-0 slippage within
model, and ML stage `gating` for >=60 sessions. On a freshly-migrated, empty
DB every one of these is honestly unmet — that is the CORRECT state of the
project right now, not a bug."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import sqlalchemy as sa

from te.domain.money import Paise
from te.engine.state import set_params_frozen_at
from te.ml import registry as ml_registry
from te.ml.gates import MaturityGate, Stage, model_promotions
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, CycleRow, TradeRow
from te.risk.live_gate import LiveUnlockGate
from te.risk.monitors import SlippageMonitor

STRATEGY = "orb"
INSTRUMENT = "NIFTY"
NOW = dt.datetime(2026, 7, 29, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'live_gate_test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _insert_model_record(session_factory, *, dsr: float, pbo: float) -> None:  # noqa: ANN001
    with session_factory() as session:
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
                dsr=dsr,
                pbo=pbo,
                n_trials_at_training=50,
                n_labeled_samples=250,
                created_at=NOW,
                notes="",
            )
        )
        session.commit()


def _insert_trade(session_factory, *, closed_at: dt.datetime, net_pnl_paise: int) -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(
            TradeRow(
                client_order_id=f"co-{closed_at.isoformat()}",
                symbol="NIFTY30JUL2624500CE",
                exchange="NFO",
                strategy=STRATEGY,
                direction="long",
                lots=1,
                lot_size=65,
                entry_premium_paise=10_000,
                exit_premium_paise=10_000 + net_pnl_paise,
                gross_pnl_paise=net_pnl_paise,
                costs_paise=0,
                net_pnl_paise=net_pnl_paise,
                exit_reason="target",
                mode="paper",
                opened_at=closed_at - dt.timedelta(minutes=30),
                closed_at=closed_at,
            )
        )
        session.commit()


def _insert_cycle(session_factory, *, ts: dt.datetime, mode: str = "paper") -> None:  # noqa: ANN001
    with session_factory() as session:
        session.add(CycleRow(ts=ts, mode=mode))
        session.commit()


def _make_clean_slippage(session_factory, *, n: int = 10) -> None:  # noqa: ANN001
    with session_factory() as session:
        monitor = SlippageMonitor(session, instrument=INSTRUMENT)
        for i in range(n):
            monitor.observe(Paise(10_000), Paise(10_000), ctx=f"clean-{i}")
        session.commit()


def _make_breached_slippage(session_factory, *, n: int = 10) -> None:  # noqa: ANN001
    import random

    rng = random.Random(3)
    with session_factory() as session:
        monitor = SlippageMonitor(session, instrument=INSTRUMENT)
        for i in range(n):
            actual = 10_000 + 100 + round(rng.gauss(0, 5))
            monitor.observe(Paise(10_000), Paise(actual), ctx=f"breach-{i}")
        session.commit()


def _promote_to_gating(session_factory, *, promoted_at: dt.datetime) -> None:  # noqa: ANN001
    gate = MaturityGate(session_factory)
    gate.set_stage(Stage.GATING, actor="test")
    with session_factory() as session:
        session.execute(
            sa.update(model_promotions)
            .where(model_promotions.c.to_stage == Stage.GATING.value)
            .values(ts=promoted_at)
        )
        session.commit()


def _satisfy_dsr_pbo(session_factory) -> None:  # noqa: ANN001
    _insert_model_record(session_factory, dsr=0.97, pbo=0.02)


def _satisfy_sessions(session_factory) -> None:  # noqa: ANN001
    frozen_at = NOW - dt.timedelta(days=200)
    with session_factory() as session:
        set_params_frozen_at(session, frozen_at)
        session.commit()
    for i in range(90):
        closed_at = frozen_at + dt.timedelta(days=i + 1)
        _insert_trade(session_factory, closed_at=closed_at, net_pnl_paise=500)


def _satisfy_slippage(session_factory) -> None:  # noqa: ANN001
    _make_clean_slippage(session_factory)


def _satisfy_gating(session_factory) -> None:  # noqa: ANN001
    promoted_at = NOW - dt.timedelta(days=100)
    _promote_to_gating(session_factory, promoted_at=promoted_at)
    for i in range(60):
        _insert_cycle(session_factory, ts=promoted_at + dt.timedelta(days=i + 1))


def test_live_gate_fails_with_specific_reasons_on_fresh_db(session_factory) -> None:  # noqa: ANN001
    gate = LiveUnlockGate(session_factory)
    result = gate.check()

    assert result.passed is False
    assert len(result.failing_conditions) >= 3
    joined = " | ".join(result.failing_conditions)
    assert "no DSR recorded yet for strategy 'orb'" in joined
    assert "no PBO recorded yet for strategy 'orb'" in joined
    assert "params_frozen_at" in joined
    assert "'shadow'" in joined
    # no generic/vague placeholders anywhere
    for condition in result.failing_conditions:
        assert condition != "not ready"
        assert "not ready" not in condition.lower()


def test_live_gate_passes_when_all_conditions_synthetically_satisfied(session_factory) -> None:  # noqa: ANN001
    _satisfy_dsr_pbo(session_factory)
    _satisfy_sessions(session_factory)
    _satisfy_slippage(session_factory)
    _satisfy_gating(session_factory)

    gate = LiveUnlockGate(session_factory)
    result = gate.check()

    assert result.failing_conditions == ()
    assert result.passed is True


@pytest.mark.parametrize(
    "missing",
    ["dsr_too_low", "pbo_too_high", "insufficient_sessions", "slippage_flagged", "gating_stage_too_short"],
)
def test_live_gate_fails_if_any_single_condition_missing(session_factory, missing: str) -> None:  # noqa: ANN001
    if missing == "dsr_too_low":
        _insert_model_record(session_factory, dsr=0.60, pbo=0.02)
    else:
        _satisfy_dsr_pbo(session_factory)

    if missing == "pbo_too_high":
        # overwrite the just-inserted clean record with a bad-PBO one
        with session_factory() as session:
            session.execute(sa.delete(ml_registry.model_registry))
            session.commit()
        _insert_model_record(session_factory, dsr=0.97, pbo=0.50)

    if missing != "insufficient_sessions":
        _satisfy_sessions(session_factory)
    else:
        # freeze params but only produce a handful of positive sessions
        frozen_at = NOW - dt.timedelta(days=200)
        with session_factory() as session:
            set_params_frozen_at(session, frozen_at)
            session.commit()
        for i in range(5):
            _insert_trade(
                session_factory, closed_at=frozen_at + dt.timedelta(days=i + 1), net_pnl_paise=500
            )

    if missing != "slippage_flagged":
        _satisfy_slippage(session_factory)
    else:
        _make_breached_slippage(session_factory)

    if missing != "gating_stage_too_short":
        _satisfy_gating(session_factory)
    else:
        promoted_at = NOW - dt.timedelta(days=5)
        _promote_to_gating(session_factory, promoted_at=promoted_at)
        for i in range(5):
            _insert_cycle(session_factory, ts=promoted_at + dt.timedelta(days=i + 1))

    gate = LiveUnlockGate(session_factory)
    result = gate.check()

    assert result.passed is False
    joined = " | ".join(result.failing_conditions)
    expectation = {
        "dsr_too_low": "DSR",
        "pbo_too_high": "PBO",
        "insufficient_sessions": "post-freeze",
        "slippage_flagged": "slippage",
        "gating_stage_too_short": "gating",
    }[missing]
    assert expectation.lower() in joined.lower()
