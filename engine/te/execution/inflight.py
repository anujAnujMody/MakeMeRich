"""`resolve_inflight()` — the ONE function that answers "what actually
happened to this order" when we're not sure (timeout, dropped ack, process
restart mid-flight). Called by a periodic in-flight check
(`ExecutionManager.check_inflight`); NEVER blind-retries `place_order`.

Isolates risk R1's resolution behind this single seam, per the plan's
requirement that a future broker swap be "a small, localized change":
`BrokerPort.query_order()` is tried first (the primary design, for a broker
that supports client-tag lookup); if it raises `NotImplementedError` (this
project's research concluded OpenAlgo does NOT support this — see
`te/broker/protocol.py`'s module docstring for the evidence), the fallback
matches `order_reports()` by `(symbol, side, quantity, submission-timestamp
window)`. An AMBIGUOUS match (more than one candidate in the window) is
treated identically to NO match — `match_pending_order` refuses to guess,
so the order stays unresolved rather than risk attaching the wrong venue
order to our local state.
"""

from __future__ import annotations

import datetime as dt

from te.broker.protocol import BrokerPort, OrderStatusReport
from te.domain.events import Side
from te.domain.orders import Order

DEFAULT_MATCH_WINDOW = dt.timedelta(seconds=5)


def match_pending_order(
    candidates: list[OrderStatusReport],
    *,
    symbol: str,
    side: Side,
    quantity: int,
    submitted_at: dt.datetime,
    window: dt.timedelta,
) -> OrderStatusReport | None:
    """Pure matching logic — deliberately its own function so it's
    independently unit-testable and so a future `OpenAlgoBroker.query_order()`
    can call it internally (via its own `order_reports()`) without
    `te.execution` needing to change."""
    matches = [
        c
        for c in candidates
        if c.symbol == symbol and c.side == side and c.quantity == quantity and abs(c.ts - submitted_at) <= window
    ]
    if len(matches) == 1:
        return matches[0]
    return None  # zero or ambiguous (>1) — never guess


def resolve_inflight(
    order: Order,
    submitted_at: dt.datetime,
    broker: BrokerPort,
    *,
    window: dt.timedelta = DEFAULT_MATCH_WINDOW,
) -> OrderStatusReport | None:
    """Ground truth for one in-flight `order`. Tries the direct client-tag
    lookup first; falls back to window-matching the full order book only if
    the broker signals it can't do a direct lookup."""
    try:
        return broker.query_order(order.client_order_id)
    except NotImplementedError:
        return match_pending_order(
            broker.order_reports(),
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            submitted_at=submitted_at,
            window=window,
        )
