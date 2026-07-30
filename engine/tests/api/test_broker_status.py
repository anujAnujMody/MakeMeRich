"""`GET /api/broker-status` — proves `connected` reflects the REAL
`WSRecorderSupervisor.is_running()` state exposed on `app.state.ws_supervisor`
by `te.api.main`'s lifespan, not a literal Phase-0 stub. Found live on
2026-07-30: this endpoint hardcoded `connected=False` forever — the Ops
page showed "Disconnected"/"Down" all day despite the broker actually
streaming ticks continuously."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import te.api.routers.broker as broker_router
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base


class _FakeSupervisor:
    def __init__(self, *, running: bool) -> None:
        self._running = running

    def is_running(self) -> bool:
        return self._running


@pytest.fixture
def isolated_client(tmp_path: Path, monkeypatch):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'broker_status_test.db'}")
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    monkeypatch.setattr(broker_router, "session_factory", sf)

    from te.api.main import app

    return TestClient(app)


def test_broker_status_reports_connected_when_the_real_supervisor_is_running(isolated_client) -> None:  # noqa: ANN001
    client = isolated_client
    with client:
        client.app.state.ws_supervisor = _FakeSupervisor(running=True)
        response = client.get("/api/broker-status")

    assert response.status_code == 200
    assert response.json()["connected"] is True
    assert response.json()["name"] == "OpenAlgo"


def test_broker_status_reports_disconnected_when_the_real_supervisor_is_not_running(isolated_client) -> None:  # noqa: ANN001
    client = isolated_client
    with client:
        client.app.state.ws_supervisor = _FakeSupervisor(running=False)
        response = client.get("/api/broker-status")

    assert response.status_code == 200
    assert response.json()["connected"] is False


def test_broker_status_reports_paper_provenance_for_real_orders_even_when_disconnected(  # noqa: ANN001
    tmp_path: Path, monkeypatch
) -> None:
    """Regression, found by review: provenance used to key off `connected`
    alone, so real orders placed earlier that day (before a mid-session
    disconnect) would be tagged `provenance=none` — "nothing real here" —
    even though `ordersToday` in the same response body is a genuine
    non-zero count."""
    from te.domain.money import Paise
    from te.domain.orders import OrderRequest
    from te.execution.idempotency import persist_initial_event
    from te.execution.store import OrderEventStore

    engine = make_engine(f"sqlite:///{tmp_path / 'broker_status_test.db'}")
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    monkeypatch.setattr(broker_router, "session_factory", sf)

    import datetime as dt

    from te.domain.clock import IST

    store = OrderEventStore(sf)
    persist_initial_event(
        store,
        client_order_id="c-1",
        request=OrderRequest(
            symbol="NIFTY30JUN2626500CE", exchange="NFO", side="BUY", quantity=65, limit_price=Paise(3_000)
        ),
        # `order_ids_today` (like every "today" aggregate in this codebase)
        # queries `on`'s calendar date as if it were a UTC date, even
        # though `on` is derived from an IST clock — a pre-existing,
        # practically-unreachable-in-production quirk (nothing is ever
        # scheduled in the IST-midnight-to-05:30 window where the two
        # dates disagree) that DOES matter for a test asserting on "today".
        # Pin the order to noon UTC on today's IST calendar-date number —
        # squarely inside whatever (correct or buggy) window the route
        # computes, regardless of what wall-clock time this test runs at.
        ts=dt.datetime.combine(dt.datetime.now(IST).date(), dt.time(12, 0), tzinfo=dt.UTC),
    )

    from te.api.main import app

    with TestClient(app) as client:
        client.app.state.ws_supervisor = _FakeSupervisor(running=False)
        response = client.get("/api/broker-status")

    body = response.json()
    assert body["connected"] is False
    assert body["ordersToday"] == 1
    assert response.headers["X-TE-Provenance"] == "paper"


def test_broker_status_reports_disconnected_when_the_supervisor_is_missing(isolated_client) -> None:  # noqa: ANN001
    """Defensive: an app.state with no `ws_supervisor` attribute at all
    (e.g. a lifespan that hasn't finished startup) must report honestly
    disconnected, never crash or fabricate `True`."""
    client = isolated_client
    with client:
        if hasattr(client.app.state, "ws_supervisor"):
            del client.app.state.ws_supervisor
        response = client.get("/api/broker-status")

    assert response.status_code == 200
    assert response.json()["connected"] is False
