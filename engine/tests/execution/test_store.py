"""te.execution.store.OrderEventStore — the append-only order_events
repository. `append()`/`events_for()`/`fold_order()` round-trip every
`OrderEvent` subtype without loss."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.domain.events import OrderAccepted, OrderFilled, OrderInitialized, OrderPartiallyFilled, OrderSubmitted
from te.domain.money import Paise
from te.execution.store import OrderEventStore
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, OrderEventRow

TS = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)


@pytest.fixture
def store(tmp_path: Path) -> OrderEventStore:
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    return OrderEventStore(session_factory)


def test_append_persists_a_row(store: OrderEventStore) -> None:
    store.append(
        OrderInitialized(client_order_id="coid-1", symbol="NIFTY", exchange="NFO", side="BUY", quantity=65, ts=TS)
    )
    events = store.events_for("coid-1")
    assert len(events) == 1
    assert isinstance(events[0], OrderInitialized)
    assert events[0].client_order_id == "coid-1"


def test_seq_increments_per_client_order_id(store: OrderEventStore) -> None:
    store.append(
        OrderInitialized(client_order_id="coid-1", symbol="NIFTY", exchange="NFO", side="BUY", quantity=65, ts=TS)
    )
    store.append(OrderSubmitted(client_order_id="coid-1", ts=TS))
    store.append(OrderAccepted(client_order_id="coid-1", venue_order_id="v-1", ts=TS))

    events = store.events_for("coid-1")
    assert [type(e).__name__ for e in events] == ["OrderInitialized", "OrderSubmitted", "OrderAccepted"]


def test_fold_order_reduces_to_current_state(store: OrderEventStore) -> None:
    store.append(
        OrderInitialized(client_order_id="coid-1", symbol="NIFTY", exchange="NFO", side="BUY", quantity=65, ts=TS)
    )
    store.append(OrderSubmitted(client_order_id="coid-1", ts=TS))
    store.append(OrderAccepted(client_order_id="coid-1", venue_order_id="v-1", ts=TS))
    store.append(
        OrderPartiallyFilled(
            client_order_id="coid-1", venue_trade_id="t-1", fill_qty=30, fill_price=Paise(10_000), ts=TS
        )
    )
    store.append(
        OrderFilled(client_order_id="coid-1", venue_trade_id="t-2", fill_qty=35, fill_price=Paise(10_050), ts=TS)
    )

    order = store.fold_order("coid-1")
    assert order.status == "FILLED"
    assert order.filled_qty == 65
    assert order.venue_order_id == "v-1"


def test_round_trips_paise_fields_as_ints(store: OrderEventStore) -> None:
    store.append(
        OrderInitialized(client_order_id="coid-1", symbol="NIFTY", exchange="NFO", side="BUY", quantity=65, ts=TS)
    )
    store.append(
        OrderFilled(client_order_id="coid-1", venue_trade_id="t-1", fill_qty=65, fill_price=Paise(12_345), ts=TS)
    )
    events = store.events_for("coid-1")
    fill = events[-1]
    assert isinstance(fill, OrderFilled)
    assert fill.fill_price == Paise(12_345)
    assert isinstance(fill.fill_price, int)


def test_events_for_unknown_id_returns_empty(store: OrderEventStore) -> None:
    assert store.events_for("nonexistent") == []


def test_all_client_order_ids(store: OrderEventStore) -> None:
    store.append(
        OrderInitialized(client_order_id="coid-1", symbol="NIFTY", exchange="NFO", side="BUY", quantity=65, ts=TS)
    )
    store.append(
        OrderInitialized(client_order_id="coid-2", symbol="SENSEX", exchange="BFO", side="SELL", quantity=20, ts=TS)
    )
    assert set(store.all_client_order_ids()) == {"coid-1", "coid-2"}


def test_unique_constraint_prevents_duplicate_seq(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'test2.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            OrderEventRow.__table__.insert().values(
                client_order_id="coid-1", seq=1, event_type="OrderInitialized", payload_json="{}", ts=TS
            )
        )
        with pytest.raises(Exception):  # noqa: B017 - sqlite IntegrityError, dialect-specific
            conn.execute(
                OrderEventRow.__table__.insert().values(
                    client_order_id="coid-1", seq=1, event_type="OrderSubmitted", payload_json="{}", ts=TS
                )
            )
