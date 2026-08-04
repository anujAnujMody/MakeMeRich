"""`OrderEventStore` — the append-only `order_events` repository.
`append()` writes one row before any network call is ever made (see
`te/execution/idempotency.py`); `events_for()`/`fold_order()` are the only
read path, always a pure `te.domain.orders.fold()` over the rows for one
`client_order_id` in `seq` order — never a separate projection that could
drift from the event log.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from te.domain.events import (
    OrderAccepted,
    OrderCancelled,
    OrderDenied,
    OrderEvent,
    OrderExpired,
    OrderFilled,
    OrderInitialized,
    OrderPartiallyFilled,
    OrderRejected,
    OrderSubmitted,
)
from te.domain.money import Paise
from te.domain.orders import Order, fold
from te.persistence.db import session_scope
from te.persistence.models import OrderEventRow

_EVENT_TYPES: dict[str, type[OrderEvent]] = {
    "OrderInitialized": OrderInitialized,
    "OrderSubmitted": OrderSubmitted,
    "OrderDenied": OrderDenied,
    "OrderAccepted": OrderAccepted,
    "OrderPartiallyFilled": OrderPartiallyFilled,
    "OrderFilled": OrderFilled,
    "OrderCancelled": OrderCancelled,
    "OrderRejected": OrderRejected,
    "OrderExpired": OrderExpired,
}

#: Fields deserialized back into `Paise`. `requested_price`/`arrival_bid`/
#: `arrival_ask` are OPTIONAL — an order written before they existed, or one
#: placed with no quote to hand, has them absent or null. `_deserialize`
#: therefore skips `None` rather than calling `Paise(None)`, so replaying the
#: existing event log keeps working unchanged.
_PAISE_FIELDS = {"fill_price", "requested_price", "arrival_bid", "arrival_ask"}


def _serialize(event: OrderEvent) -> str:
    payload = dataclasses.asdict(event)
    payload["ts"] = event.ts.isoformat()
    return json.dumps(payload)


def _deserialize(event_type: str, payload_json: str) -> OrderEvent:
    cls = _EVENT_TYPES[event_type]
    payload = json.loads(payload_json)
    payload["ts"] = dt.datetime.fromisoformat(payload["ts"])
    for field in _PAISE_FIELDS:
        if payload.get(field) is not None:
            payload[field] = Paise(payload[field])
    return cls(**payload)


class OrderEventStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def append(self, event: OrderEvent) -> None:
        """Persists `event` as the next `seq` for its `client_order_id`,
        committing immediately. Callers that need "persist before network
        call" ordering rely on this committing synchronously — see
        `te.execution.idempotency.persist_initial_event`."""
        with session_scope(self._session_factory) as session:
            next_seq = (
                session.execute(
                    select(func.coalesce(func.max(OrderEventRow.seq), 0)).where(
                        OrderEventRow.client_order_id == event.client_order_id
                    )
                ).scalar_one()
                + 1
            )
            row = OrderEventRow(
                client_order_id=event.client_order_id,
                seq=next_seq,
                event_type=type(event).__name__,
                payload_json=_serialize(event),
                ts=event.ts,
            )
            session.add(row)

    def events_for(self, client_order_id: str) -> list[OrderEvent]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(OrderEventRow)
                    .where(OrderEventRow.client_order_id == client_order_id)
                    .order_by(OrderEventRow.seq)
                )
                .scalars()
                .all()
            )
            return [_deserialize(row.event_type, row.payload_json) for row in rows]

    def fold_order(self, client_order_id: str) -> Order:
        events = self.events_for(client_order_id)
        if not events:
            raise KeyError(f"no events for client_order_id={client_order_id!r}")
        return fold(events)

    def all_client_order_ids(self) -> list[str]:
        with self._session_factory() as session:
            rows = session.execute(select(OrderEventRow.client_order_id).distinct()).scalars().all()
            return list(rows)
