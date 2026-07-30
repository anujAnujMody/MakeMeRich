"""`Order` aggregate = `fold(events)` — a pure reduction over an event list,
no I/O. Preparation for Phase 3's execution core, which will own the
persistence/broker machinery (append-only `order_events` store, idempotency,
reconciliation) around these types; this module only knows how to compute
the current state from a sequence of events.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from typing import Literal

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
    Side,
)
from te.domain.money import Paise

OrderType = Literal["MARKET", "LIMIT"]

OrderStatus = Literal[
    "INITIALIZED",
    "SUBMITTED",
    "DENIED",
    "ACCEPTED",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELED",
    "REJECTED",
    "EXPIRED",
]


@dataclass(frozen=True)
class Order:
    client_order_id: str
    symbol: str
    exchange: str
    side: Literal["BUY", "SELL"]
    quantity: int
    status: OrderStatus
    filled_qty: int = 0
    venue_order_id: str | None = None


@dataclass(frozen=True)
class OrderRequest:
    """What a caller (e.g. `te.risk.sizing`, later phases) asks
    `ExecutionManager.submit()` for. Deliberately has no `client_order_id`/
    `ts` — those are minted by the execution core, never by the caller, so
    idempotency can't be bypassed by a caller supplying its own id.

    `reduce_only` is the caller's explicit declaration that this order can
    only shrink/close existing exposure, never open new exposure — what
    `ExecutionManager.submit()` checks to decide whether a halt should
    block it (see that method's docstring). Declared explicitly by the
    caller that actually knows the intent (`te.engine.cycle._close_position`
    sets it) rather than inferred from `side`, so a future order path this
    project doesn't have yet (a hedge, a spread leg) can't silently bypass
    the halt just because it happens to submit a SELL."""

    symbol: str
    exchange: str
    side: Side
    quantity: int
    order_type: OrderType = "MARKET"
    limit_price: Paise | None = None
    reduce_only: bool = False


@dataclass(frozen=True)
class OrderIntent:
    """A fully-formed, ready-to-send order — `OrderRequest` plus the
    execution core's minted `client_order_id` and submission timestamp.
    This is the type `te.broker.protocol.BrokerPort.place_order()` accepts;
    both `te.broker` and `te.execution` may import it since it lives in
    `te.domain`."""

    client_order_id: str
    symbol: str
    exchange: str
    side: Side
    quantity: int
    order_type: OrderType
    limit_price: Paise | None
    ts: dt.datetime


def fold(events: list[OrderEvent]) -> Order:
    """Pure reduction over `events` into the `Order`'s current state. The
    first event must be `OrderInitialized`."""
    if not events:
        raise ValueError("fold() requires at least one event")
    first = events[0]
    if not isinstance(first, OrderInitialized):
        raise ValueError("the first event must be OrderInitialized")

    order = Order(
        client_order_id=first.client_order_id,
        symbol=first.symbol,
        exchange=first.exchange,
        side=first.side,
        quantity=first.quantity,
        status="INITIALIZED",
    )
    for event in events[1:]:
        order = _apply(order, event)
    return order


def _apply(order: Order, event: OrderEvent) -> Order:
    if isinstance(event, OrderSubmitted):
        return replace(order, status="SUBMITTED")
    if isinstance(event, OrderDenied):
        return replace(order, status="DENIED")
    if isinstance(event, OrderAccepted):
        return replace(order, status="ACCEPTED", venue_order_id=event.venue_order_id)
    if isinstance(event, OrderPartiallyFilled):
        return replace(order, status="PARTIALLY_FILLED", filled_qty=order.filled_qty + event.fill_qty)
    if isinstance(event, OrderFilled):
        return replace(order, status="FILLED", filled_qty=order.filled_qty + event.fill_qty)
    if isinstance(event, OrderCancelled):
        return replace(order, status="CANCELED")
    if isinstance(event, OrderRejected):
        return replace(order, status="REJECTED")
    if isinstance(event, OrderExpired):
        return replace(order, status="EXPIRED")
    raise TypeError(f"unknown OrderEvent type: {type(event)!r}")
