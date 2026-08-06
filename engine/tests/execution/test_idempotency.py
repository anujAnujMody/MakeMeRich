"""te.execution.idempotency — mint a client_order_id and persist it BEFORE
any network call. This ordering is Phase 3's central discipline."""

from __future__ import annotations

from pathlib import Path

import pytest

from te.domain.orders import OrderRequest
from te.execution.idempotency import mint_client_order_id, persist_initial_event
from te.execution.store import OrderEventStore
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base


@pytest.fixture
def store(tmp_path: Path) -> OrderEventStore:
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return OrderEventStore(make_session_factory(engine))


def test_mint_client_order_id_is_unique() -> None:
    ids = {mint_client_order_id() for _ in range(1000)}
    assert len(ids) == 1000


def test_persist_initial_event_writes_before_returning(store: OrderEventStore) -> None:
    request = OrderRequest(symbol="NIFTY", exchange="NFO", side="BUY", quantity=65)
    client_order_id = mint_client_order_id()

    persist_initial_event(store, client_order_id=client_order_id, request=request)

    events = store.events_for(client_order_id)
    assert len(events) == 1
    assert events[0].client_order_id == client_order_id
