"""Mint a `client_order_id` and PERSIST it before any network call is made.

This ordering is Phase 3's central discipline (copied from NautilusTrader's
order lifecycle): if the process dies or the broker call times out the
instant after `place_order()` is invoked, the fact that we *intended* to
place this order is already durable, so a restart/reconciliation pass can
find it and resolve it — never silently lose an in-flight order.
`ExecutionManager.submit()` in `te/execution/manager.py` is the only caller;
this module is kept separate so the ordering guarantee has one obvious,
independently-testable seam (`test_client_order_id_persisted_before_network`).
"""

from __future__ import annotations

import datetime as dt
import uuid

from te.domain.events import OrderInitialized
from te.domain.orders import OrderRequest
from te.execution.store import OrderEventStore


def mint_client_order_id() -> str:
    """A fresh, unguessable idempotency key. UUID4 — no broker interaction,
    no shared counter, so minting never races or blocks."""
    return str(uuid.uuid4())


def persist_initial_event(
    store: OrderEventStore,
    *,
    client_order_id: str,
    request: OrderRequest,
    ts: dt.datetime | None = None,
) -> OrderInitialized:
    """Writes the `OrderInitialized` event — the durable record that this
    order was ever intended — and returns it. Must be called, and must
    return, before `BrokerPort.place_order()` is ever invoked."""
    event = OrderInitialized(
        client_order_id=client_order_id,
        symbol=request.symbol,
        exchange=request.exchange,
        side=request.side,
        quantity=request.quantity,
        ts=ts or dt.datetime.now(dt.UTC),
        # Captured here, at t=0, because it cannot be recovered later — see
        # `OrderInitialized`. This function already runs BEFORE any network
        # call, which is exactly the instant the benchmark must describe.
        requested_price=request.limit_price,
        arrival_bid=request.arrival_bid,
        arrival_ask=request.arrival_ask,
    )
    store.append(event)
    return event
