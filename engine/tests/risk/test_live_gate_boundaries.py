"""`te.risk.live_gate` — exact boundary mutants on the strict `>`/`<`
comparisons `test_live_gate.py` never pins to the instant: a trade/cycle
happening exactly AT the anchor timestamp (`params_frozen_at` /
`model_promotions.ts`), a day whose net P&L is exactly 0, and a slippage
sample of exactly `MIN_SLIPPAGE_OBSERVATIONS`."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import sqlalchemy as sa

from te.domain.money import Paise
from te.engine.state import set_params_frozen_at
from te.ml.gates import MaturityGate, Stage, model_promotions
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, CycleRow, TradeRow
from te.risk.live_gate import MIN_SLIPPAGE_OBSERVATIONS, LiveUnlockGate
from te.risk.monitors import SlippageMonitor

STRATEGY = "orb"
INSTRUMENT = "NIFTY"
NOW = dt.datetime(2026, 7, 29, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'live_gate_boundaries_test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _insert_model_record(session_factory, *, dsr: float, pbo: float) -> None:  # noqa: ANN001
    from te.ml import registry as ml_registry

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


def _promote_to_gating(session_factory, *, promoted_at: dt.datetime) -> None:  # noqa: ANN001
    gate = MaturityGate(session_factory)
    gate.set_stage(Stage.GATING, actor="test")
    with session_factory() as session:
        session.execute(
            sa.update(model_promotions).where(model_promotions.c.to_stage == Stage.GATING.value).values(ts=promoted_at)
        )
        session.commit()


def _make_clean_slippage(session_factory, *, n: int) -> None:  # noqa: ANN001
    import random

    rng = random.Random(11)
    with session_factory() as session:
        monitor = SlippageMonitor(session, instrument=INSTRUMENT)
        for i in range(n):
            actual = 10_000 + round(rng.gauss(0, 5))
            monitor.observe(Paise(10_000), Paise(actual), ctx=f"clean-{i}")
        session.commit()


def test_a_trade_closed_exactly_at_params_frozen_at_does_not_count_as_post_freeze(session_factory) -> None:  # noqa: ANN001
    """`_post_freeze_positive_sessions` filters `closed_at > since` — STRICTLY
    after. A trade closed at exactly `params_frozen_at` was earned against
    the OLD (pre-freeze) parameters, the same instant, and must not count."""
    frozen_at = NOW - dt.timedelta(days=10)
    with session_factory() as session:
        set_params_frozen_at(session, frozen_at)
        session.add(
            TradeRow(
                client_order_id="c-at-freeze",
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
                opened_at=frozen_at - dt.timedelta(minutes=30),
                closed_at=frozen_at,  # exactly at the anchor
            )
        )
        session.commit()

    from te.risk.live_gate import _post_freeze_positive_sessions

    with session_factory() as session:
        n = _post_freeze_positive_sessions(session, strategy=STRATEGY, since=frozen_at)

    assert n == 0


def test_a_day_with_exactly_zero_net_pnl_does_not_count_as_positive(session_factory) -> None:  # noqa: ANN001
    """`sum(1 for net in by_date.values() if net > 0)` — a breakeven day
    (net == 0 exactly) is not a net-POSITIVE session."""
    frozen_at = NOW - dt.timedelta(days=10)
    closed = frozen_at + dt.timedelta(days=1)
    with session_factory() as session:
        set_params_frozen_at(session, frozen_at)
        session.add(
            TradeRow(
                client_order_id="c-breakeven",
                symbol="NIFTY30JUL2624500CE",
                exchange="NFO",
                strategy=STRATEGY,
                direction="long",
                lots=1,
                lot_size=65,
                entry_premium_paise=10_000,
                exit_premium_paise=10_000,
                gross_pnl_paise=0,
                costs_paise=0,
                net_pnl_paise=0,
                exit_reason="target",
                mode="paper",
                opened_at=closed - dt.timedelta(minutes=30),
                closed_at=closed,
            )
        )
        session.commit()

    from te.risk.live_gate import _post_freeze_positive_sessions

    with session_factory() as session:
        n = _post_freeze_positive_sessions(session, strategy=STRATEGY, since=frozen_at)

    assert n == 0


def test_a_cycle_exactly_at_the_gating_promotion_instant_does_not_count_as_a_gating_session(session_factory) -> None:  # noqa: ANN001
    """`_paper_sessions_since` filters `CycleRow.ts > since` — STRICTLY after
    the promotion instant, matching `_post_freeze_positive_sessions`'s own
    strict-after convention for the same reason: a cycle at exactly that
    instant ran under the OLD stage."""
    promoted_at = NOW - dt.timedelta(days=5)
    with session_factory() as session:
        session.add(CycleRow(ts=promoted_at, mode="paper"))  # exactly at the anchor
        session.commit()

    from te.risk.live_gate import _paper_sessions_since

    with session_factory() as session:
        n = _paper_sessions_since(session, since=promoted_at)

    assert n == 0


def test_slippage_sample_of_exactly_the_minimum_size_is_not_flagged_as_too_small(session_factory) -> None:  # noqa: ANN001
    """`status.n < MIN_SLIPPAGE_OBSERVATIONS` — a sample of EXACTLY the
    minimum size must not be refused for being too small."""
    _insert_model_record(session_factory, dsr=0.97, pbo=0.02)
    frozen_at = NOW - dt.timedelta(days=200)
    with session_factory() as session:
        set_params_frozen_at(session, frozen_at)
        session.commit()
    for i in range(90):
        with session_factory() as session:
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
                    opened_at=frozen_at + dt.timedelta(days=i + 1) - dt.timedelta(minutes=30),
                    closed_at=frozen_at + dt.timedelta(days=i + 1),
                )
            )
            session.commit()
    promoted_at = NOW - dt.timedelta(days=100)
    _promote_to_gating(session_factory, promoted_at=promoted_at)
    for i in range(60):
        with session_factory() as session:
            session.add(CycleRow(ts=promoted_at + dt.timedelta(days=i + 1), mode="paper"))
            session.commit()
    _make_clean_slippage(session_factory, n=MIN_SLIPPAGE_OBSERVATIONS)  # exactly the minimum

    result = LiveUnlockGate(session_factory).check()

    joined = " | ".join(result.failing_conditions)
    assert "required slippage observations" not in joined
