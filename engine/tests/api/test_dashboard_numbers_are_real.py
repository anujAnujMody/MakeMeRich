"""Three dashboard fields that were literal constants rather than
measurements (Phase 0 item 4 of `docs/FableImprovements.md`):
`mlStage="shadow"`, `nextCheckInSeconds=0`, `currentDrawdownPct=0.0`.

A constant that happens to be right today is indistinguishable from a
measurement until the day it is wrong — and each of these three is wrong
precisely when it matters most (the model has been promoted; the scheduler
has stalled; the account is in drawdown). Each test therefore moves the
underlying state and asserts the endpoint follows it, which a hardcoded
value cannot do.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import te.api.routers.dashboard as dashboard_router
import te.api.routers.engine as engine_router
from te.domain.clock import IST
from te.domain.money import Paise
from te.engine.state import (
    AccountGuardrails,
    PipelineStageTiming,
    set_guardrails,
    set_last_cycle_pipeline,
    set_peak_equity_paise,
    set_run_state,
)
from te.ml.gates import MaturityGate, Stage
from te.persistence.db import make_engine, make_session_factory, session_scope
from te.persistence.models import Base, TradeRow

#: Capital and the peak-equity watermark must be set together: equity is
#: `capital + realized + unrealized`, so a peak recorded under one capital
#: figure and compared against another reports the DIFFERENCE as drawdown.
#: In production `set_guardrails` rebases the watermark whenever capital is
#: edited, keeping the two consistent; a test that seeds only one of them is
#: modelling a state the engine never reaches.
CAPITAL = Paise(30_000_000)  # Rs 3,00,000


def _seed_capital(session) -> None:  # noqa: ANN001
    set_guardrails(
        session,
        AccountGuardrails(
            capital=CAPITAL,
            max_daily_loss=Paise(1_000_000),
            max_position_size_pct=Decimal(20),
            max_drawdown_pct=Decimal(15),
            max_trades_per_day=20,
            max_concurrent_positions=5,
            risk_per_trade_pct=Decimal(1),
        ),
    )
    set_peak_equity_paise(session, CAPITAL)

_IST_TODAY = dt.datetime.now(IST).date()
NOW = dt.datetime(_IST_TODAY.year, _IST_TODAY.month, _IST_TODAY.day, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'honesty.db'}")
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    for module in (dashboard_router, engine_router):
        monkeypatch.setattr(module, "session_factory", sf)

    from te.api.main import app

    return TestClient(app), sf


def test_ml_stage_follows_a_real_promotion(isolated) -> None:
    """The single most safety-relevant number on the page: the literal
    `"shadow"` agreed with reality only until something was promoted, at
    which point the dashboard would have kept reporting a model that cannot
    touch a decision while it was actually gating them."""
    client, sf = isolated

    assert client.get("/api/dashboard/snapshot").json()["mlStage"] == "shadow"

    MaturityGate(sf).set_stage(Stage.ADVISORY, actor="test")

    assert client.get("/api/dashboard/snapshot").json()["mlStage"] == "advisory"


def test_next_check_counts_down_from_the_last_real_cycle(isolated) -> None:
    client, sf = isolated
    with session_scope(sf) as session:
        set_run_state(session, "running")
        set_last_cycle_pipeline(
            session,
            cycle_id=1,
            as_of=dt.datetime.now(dt.UTC),
            stages={"fetch": PipelineStageTiming(reached=True, elapsed_ms=1)},
        )

    seconds = client.get("/api/dashboard/snapshot").json()["nextCheckInSeconds"]
    assert 0 < seconds <= 60, f"expected a real countdown within the 1-minute interval, got {seconds}"


def test_next_check_is_zero_when_the_engine_is_paused(isolated) -> None:
    """`0` must mean "nothing is due", not "the countdown reached zero on a
    healthy engine" — otherwise a paused engine looks perpetually about to
    run."""
    client, sf = isolated
    with session_scope(sf) as session:
        set_run_state(session, "paused")
        set_last_cycle_pipeline(
            session,
            cycle_id=1,
            as_of=dt.datetime.now(dt.UTC),
            stages={"fetch": PipelineStageTiming(reached=True, elapsed_ms=1)},
        )

    assert client.get("/api/dashboard/snapshot").json()["nextCheckInSeconds"] == 0


def test_current_drawdown_reflects_a_real_loss(isolated) -> None:
    """Seeds a peak watermark and a realized loss, then asserts the reported
    percentage is the real distance below the peak — the number the
    drawdown breaker itself compares against."""
    client, sf = isolated
    with session_scope(sf) as session:
        _seed_capital(session)
        session.add(
            TradeRow(
                client_order_id="co-dd",
                symbol="NIFTY30JUL2624500CE",
                exchange="NFO",
                strategy="orb",
                direction="long_call",
                lots=1,
                lot_size=65,
                entry_premium_paise=10_000,
                exit_premium_paise=5_000,
                gross_pnl_paise=-3_000_000,
                costs_paise=0,
                net_pnl_paise=-3_000_000,  # -Rs 30,000 against a Rs 3,00,000 peak
                exit_reason="stop",
                mode="paper",
                opened_at=NOW - dt.timedelta(minutes=30),
                closed_at=NOW,
            )
        )

    got = client.get("/api/engine/health").json()["currentDrawdownPct"]
    assert got == pytest.approx(10.0, abs=0.01), "Rs 30,000 down from a Rs 3,00,000 peak is 10%"


def test_current_drawdown_is_zero_at_a_new_high(isolated) -> None:
    client, sf = isolated
    with session_scope(sf) as session:
        _seed_capital(session)

    assert client.get("/api/engine/health").json()["currentDrawdownPct"] == 0.0


def test_reading_health_never_moves_the_watermark(isolated) -> None:
    """Ratcheting the peak is the trading loop's job. A dashboard poll that
    moved it would silently reset the drawdown a breaker is measuring."""
    client, sf = isolated
    with session_scope(sf) as session:
        _seed_capital(session)

    client.get("/api/engine/health")

    from te.engine.state import get_peak_equity_paise

    with sf() as session:
        assert get_peak_equity_paise(session) == CAPITAL
