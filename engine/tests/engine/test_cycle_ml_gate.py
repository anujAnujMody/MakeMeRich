"""The single most important test in Phase 6: ML output swinging 0.0 -> 1.0
must change no order, size, or skip reason, at any stage below `gating`.
Wires the REAL `te.ml.gates.MaturityGate.influence()` through a stub
`MLHook` that forces `p` (mirroring the plan's `forced_p`/`forced_stage`
adapter), so this is a mechanical proof through the actual gate logic, not a
mock of it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from te.broker.simulated import SimulatedBroker
from te.data.barstore import BAR_COLUMNS, BarStore
from te.data.charges_loader import load_charge_rate_table
from te.domain.clock import IST
from te.domain.costs import CostModel, select_rates
from te.domain.geometry import AbsolutePointGeometry
from te.domain.money import Paise
from te.engine.cycle import CycleConfig, run_entry_cycle
from te.execution.manager import ExecutionManager
from te.execution.store import OrderEventStore
from te.ml.gates import MaturityGate, MLInfluence, Stage
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, OpenPositionRow, SkippedSignalRow
from te.risk.limits import RiskLimitsConfig

_CHARGES_PATH = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"
INSTRUMENT = "NIFTY30JUN2626500CE"
EXCHANGE = "NFO"
ON = dt.date(2026, 7, 29)


class _NoLimiter:
    def acquire(self, n: int = 1) -> None:
        pass


@dataclass
class _ForcedPHook:
    """Test double standing in for `te.ml.predict.ShadowMLHook`: skips
    feature-building/model inference entirely and calls the REAL
    `MaturityGate.influence()` with a forced `p`, so the resulting
    `MLInfluence` (and everything downstream in `cycle.py`) exercises the
    actual gate logic under test."""

    gate: MaturityGate
    forced_p: float

    def evaluate(self, *, instrument: str, as_of: dt.datetime, cycle_id: int) -> MLInfluence:
        return self.gate.influence(self.forced_p)


def _bar(event_ts: dt.datetime, *, o: float, h: float, low: float, c: float, v: int) -> dict[str, object]:
    return {
        "symbol": INSTRUMENT,
        "exchange": EXCHANGE,
        "event_ts": event_ts,
        "interval": "1m",
        "o": o,
        "h": h,
        "l": low,
        "c": c,
        "v": v,
        "oi": 0,
        "ingested_at": event_ts,
        "source": "test",
    }


def _open(minute: int) -> dt.datetime:
    base = dt.datetime(2026, 7, 29, 9, 15, tzinfo=IST)
    return base + dt.timedelta(minutes=minute)


def _breakout_store(tmp_path: Path) -> BarStore:
    # Matches `tests/engine/test_cycle.py`'s `_breakout_store`: `OrbStrategy`
    # defaults to a 60-minute opening range, so the breakout bar must sit at
    # `_open(60)` (10:15) or later. The previous `_open(15)` breakout bar sat
    # INSIDE the opening range window, so `breakout_bars` was always empty and
    # every run below — rule-only, forced-p, and exploding-hook alike —
    # skipped identically on "opening range not yet formed". Both tests in
    # this file compared `(0, 1) == (0, 1)`, which is true no matter what the
    # ML block actually does; see `test_a_trade_actually_fires_here_precondition`.
    store = BarStore(tmp_path / "bars")
    rows = [
        _bar(_open(0), o=30, h=32, low=28, c=30, v=1_000),
        _bar(_open(1), o=30, h=31, low=29, c=30.2, v=1_000),
        _bar(_open(2), o=30, h=31, low=29, c=30.1, v=1_000),
        _bar(_open(60), o=30, h=38, low=30, c=36, v=2_000),
    ]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))
    return store


@pytest.fixture
def cost_model() -> CostModel:
    table = load_charge_rate_table(_CHARGES_PATH)
    return CostModel(select_rates(table, ON))


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'cycle_ml_gate_test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def execution(session_factory, cost_model: CostModel):  # noqa: ANN001, ANN201
    broker = SimulatedBroker(cost_model=cost_model, on=ON)
    store = OrderEventStore(session_factory)
    return ExecutionManager(session_factory, store, broker, _NoLimiter())


def _config() -> CycleConfig:
    return CycleConfig(
        mode="paper",
        strategy_name="orb",
        instruments=(INSTRUMENT,),
        exchange=EXCHANGE,
        lot_size=65,
        capital=Paise(2_500_000),
        risk_budget_pct=Decimal(2),
        min_edge_multiple=Decimal("1.2"),
        exit_geometry=AbsolutePointGeometry(
            stop_distance=Paise(700), target_distance=Paise(1_500), trailing_distance=Paise(300)
        ),
        max_hold=dt.timedelta(hours=3),
        hard_exit_by=dt.time(15, 20),
        risk_limits=RiskLimitsConfig(
            max_daily_loss_paise=Paise(10_000_00), max_concurrent_positions=5, max_trades_per_day=20
        ),
    )


def _set_stage(session_factory, stage: Stage) -> None:  # noqa: ANN001
    MaturityGate(session_factory).set_stage(stage, actor="test")


def _decision(session_factory) -> tuple[int, int, tuple[str, ...]]:  # noqa: ANN001
    """`(open_positions_count, skipped_signals_count, skip_reasons)` — the
    observable "trading decision" this test asserts is byte-identical across
    stage x p. Skip reasons are included (not just their count) so a mutation
    that changes WHY a signal skipped, without changing the counts, is still
    caught."""
    with session_factory() as session:
        return (
            session.query(OpenPositionRow).count(),
            session.query(SkippedSignalRow).count(),
            tuple(sorted(row.reason for row in session.query(SkippedSignalRow).all())),
        )


def _run(session_factory, execution, cost_model, tmp_path, *, ml_hook):  # noqa: ANN001
    store = _breakout_store(tmp_path)
    run_entry_cycle(
        session_factory=session_factory,
        store=store,
        execution=execution,
        cost_model=cost_model,
        config=_config(),
        as_of=_open(61),
        ml_hook=ml_hook,
    )
    return _decision(session_factory)


@pytest.mark.parametrize("stage", [Stage.SHADOW, Stage.ADVISORY])
@pytest.mark.parametrize("p", [0.0, 0.5, 1.0])
def test_ml_cannot_affect_decisions_below_gating(
    stage: Stage,
    p: float,
    session_factory,
    execution,
    cost_model: CostModel,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    """The load-bearing test: the SAME cycle input run through shadow and
    advisory stages, at p=0.0, 0.5, and 1.0, produces a trading decision
    byte-identical to the rule-only decision (no `ml_hook` at all)."""
    rule_only_decision = _run(session_factory, execution, cost_model, tmp_path, ml_hook=None)
    # A precondition, not the real assertion: proves this fixture actually
    # trades before the rest of the test relies on that. Without it, a
    # stale/broken fixture that always skips makes `ml_decision ==
    # rule_only_decision` vacuously true for ANY ML behaviour — the exact
    # failure mode this test previously had (see `_breakout_store`).
    assert rule_only_decision[:2] == (1, 0), (
        f"fixture did not trade as expected — got {rule_only_decision}, so the comparison below would be vacuous"
    )

    # Fresh DB for the ML-influenced run so both runs start from the same
    # empty state and are directly comparable.
    fresh_engine = make_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    Base.metadata.create_all(fresh_engine)
    fresh_session_factory = make_session_factory(fresh_engine)
    fresh_broker = SimulatedBroker(cost_model=cost_model, on=ON)
    fresh_execution = ExecutionManager(
        fresh_session_factory, OrderEventStore(fresh_session_factory), fresh_broker, _NoLimiter()
    )

    _set_stage(fresh_session_factory, stage)
    gate = MaturityGate(fresh_session_factory)
    hook = _ForcedPHook(gate=gate, forced_p=p)

    ml_decision = _run(fresh_session_factory, fresh_execution, cost_model, tmp_path, ml_hook=hook)

    assert ml_decision == rule_only_decision
    with fresh_session_factory() as session:
        if ml_decision[0] > 0:
            row = session.query(OpenPositionRow).one()
            assert row.lots > 0
            with session_factory() as baseline_session:
                baseline_row = baseline_session.query(OpenPositionRow).one()
                assert row.lots == baseline_row.lots


@dataclass
class _ExplodingHook:
    """A hook whose `evaluate` raises — the realistic failure, not a
    contrived one. `ShadowMLHook.evaluate` builds a feature row from bars via
    `build_training_set`, and a short lookback, a missing India VIX series or
    an unpickleable artifact all raise from inside it."""

    def evaluate(self, *, instrument: str, as_of: dt.datetime, cycle_id: int) -> MLInfluence:
        raise RuntimeError("feature build failed")


def test_a_failing_ml_hook_cannot_stop_the_cycle_trading(
    session_factory,
    execution,
    cost_model: CostModel,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    """The companion to the test above, in the direction nobody checks.

    `MaturityGate` proves ML cannot CHANGE a decision below `gating`. It
    says nothing about ML PREVENTING one — and before this was guarded, an
    exception from `evaluate` propagated straight out of `run_entry_cycle`,
    aborting the entry pass for every instrument in it. That would have
    turned the shadow layer, whose entire purpose is to be inert, into a
    single point of failure for trading, at the exact moment it was first
    wired into production.
    """
    rule_only_decision = _run(session_factory, execution, cost_model, tmp_path, ml_hook=None)
    assert rule_only_decision[:2] == (1, 0), (
        f"fixture did not trade as expected — got {rule_only_decision}, so the comparison below would be vacuous"
    )

    fresh_engine = make_engine(f"sqlite:///{tmp_path / 'exploding.db'}")
    Base.metadata.create_all(fresh_engine)
    fresh_session_factory = make_session_factory(fresh_engine)
    fresh_execution = ExecutionManager(
        fresh_session_factory,
        OrderEventStore(fresh_session_factory),
        SimulatedBroker(cost_model=cost_model, on=ON),
        _NoLimiter(),
    )

    exploded_decision = _run(
        fresh_session_factory, fresh_execution, cost_model, tmp_path, ml_hook=_ExplodingHook()
    )

    assert exploded_decision == rule_only_decision
