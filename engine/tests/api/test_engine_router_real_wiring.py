"""Proves the plan's Tier 1 (`/api/engine/guardrails`) and Tier 2
(real pause/resume/reset-drawdown-breaker) fixes from the "Dashboard<->engine
wiring remediation" section — each of these used to flip a process-local
`state.engine_health` field the paper-trading loop never read (Pause did not
pause anything; Reset-breaker cleared a display number while the real
kill-switch had no API path to clear at all). Runs against an isolated DB,
same pattern as `tests/api/test_mode_gate.py`."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import te.api.routers.engine as engine_router
from te.engine.state import get_run_state
from te.execution.halt import is_halted
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base
from te.risk import killswitch
from te.risk.killswitch import trip as trip_killswitch


@pytest.fixture
def isolated_client(tmp_path: Path, monkeypatch):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'engine_router_test.db'}")
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    monkeypatch.setattr(engine_router, "session_factory", sf)
    killswitch.reset_in_process_cache()

    from te.api.main import app

    return TestClient(app), sf


def test_pause_actually_persists_paused_run_state(isolated_client) -> None:  # noqa: ANN001
    client, sf = isolated_client
    response = client.post("/api/engine/pause")
    assert response.status_code == 200
    assert response.json()["runState"] == "paused"
    with sf() as session:
        assert get_run_state(session) == "paused"


def test_resume_actually_persists_running_run_state(isolated_client) -> None:  # noqa: ANN001
    client, sf = isolated_client
    client.post("/api/engine/pause")
    response = client.post("/api/engine/resume")
    assert response.status_code == 200
    assert response.json()["runState"] == "running"
    with sf() as session:
        assert get_run_state(session) == "running"


def test_reset_breaker_clears_the_real_halt_flag(isolated_client) -> None:  # noqa: ANN001
    client, sf = isolated_client
    with sf() as session:
        trip_killswitch(session, "test halt")
        session.commit()
    assert client.get("/api/engine/health").json()["drawdownBreakerTripped"] is True

    response = client.post("/api/engine/reset-drawdown-breaker")
    assert response.status_code == 200
    assert response.json()["drawdownBreakerTripped"] is False
    with sf() as session:
        assert is_halted(session) is False
    # the in-process layer 1 flag must ALSO clear, not just the DB flag
    assert killswitch.is_tripped_in_process() is False


def test_relogin_broker_honestly_reports_not_configured(isolated_client) -> None:  # noqa: ANN001
    """The test environment has no OpenAlgo-app/Angel credentials configured
    (see `te.settings.Settings`'s new optional fields) — the endpoint must
    report that honestly (`success:false`, `stage:"not_configured"`), never
    a fabricated success."""
    client, _sf = isolated_client
    response = client.post("/api/engine/relogin-broker")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["stage"] == "not_configured"


def test_guardrails_get_returns_env_defaults_when_unset(isolated_client) -> None:  # noqa: ANN001
    client, _sf = isolated_client
    body = client.get("/api/engine/guardrails").json()
    assert body["capitalRupees"] > 0
    assert body["maxTradesPerDay"] > 0


def test_guardrails_put_then_get_round_trips(isolated_client) -> None:  # noqa: ANN001
    client, _sf = isolated_client
    payload = {
        "capitalRupees": 30000,
        "maxDailyLossRupees": 5000,
        "maxPositionSizePct": 25,
        "maxDrawdownPct": 10,
        "maxTradesPerDay": 5,
        "maxConcurrentPositions": 2,
        "riskPerTradePct": 1.5,
    }
    put_response = client.put("/api/engine/guardrails", json=payload)
    assert put_response.status_code == 200
    assert put_response.json()["capitalRupees"] == 30000

    get_response = client.get("/api/engine/guardrails")
    assert get_response.json() == put_response.json()


def test_instruments_get_returns_env_defaults_when_unset(isolated_client) -> None:  # noqa: ANN001
    client, _sf = isolated_client
    body = client.get("/api/engine/instruments").json()
    assert isinstance(body["instruments"], list)


def test_instruments_put_then_get_round_trips_with_per_instrument_lot_size(isolated_client) -> None:  # noqa: ANN001
    client, _sf = isolated_client
    payload = {
        "instruments": [
            {"symbol": "NIFTY", "exchange": "NFO", "lotSize": 65, "active": True},
            {"symbol": "BANKNIFTY", "exchange": "NFO", "lotSize": 30, "active": True},
            {"symbol": "SENSEX", "exchange": "BFO", "lotSize": 20, "active": False},
        ]
    }
    put_response = client.put("/api/engine/instruments", json=payload)
    assert put_response.status_code == 200
    body = put_response.json()["instruments"]
    by_symbol = {i["symbol"]: i for i in body}
    assert by_symbol["NIFTY"]["lotSize"] == 65
    assert by_symbol["BANKNIFTY"]["lotSize"] == 30
    assert by_symbol["SENSEX"]["active"] is False

    get_response = client.get("/api/engine/instruments")
    assert get_response.json() == put_response.json()


def test_instruments_put_rejects_duplicate_symbol_exchange_pair(isolated_client) -> None:  # noqa: ANN001
    """`TestClient` re-raises unhandled exceptions by default (rather than
    converting them to a 500 response) — this asserts the validation
    actually fires, matching `set_instrument_selections`'s own unit-tested
    behaviour, not FastAPI's default error-handling wrapper."""
    client, _sf = isolated_client
    payload = {
        "instruments": [
            {"symbol": "NIFTY", "exchange": "NFO", "lotSize": 65, "active": True},
            {"symbol": "NIFTY", "exchange": "NFO", "lotSize": 70, "active": True},
        ]
    }
    with pytest.raises(ValueError, match="duplicate instrument selection"):
        client.put("/api/engine/instruments", json=payload)


def test_guardrails_put_rejects_zero_capital(isolated_client) -> None:  # noqa: ANN001
    client, _sf = isolated_client
    payload = {
        "capitalRupees": 0,
        "maxDailyLossRupees": 5000,
        "maxPositionSizePct": 25,
        "maxDrawdownPct": 10,
        "maxTradesPerDay": 5,
        "maxConcurrentPositions": 2,
        "riskPerTradePct": 1.5,
    }
    response = client.put("/api/engine/guardrails", json=payload)
    assert response.status_code == 422
