"""`BrokerPort` — the only broker abstraction `te.execution` is allowed to
depend on. Structural typing (a `Protocol`, not an ABC) so `te.broker.
simulated.SimulatedBroker` (this phase's only wired venue) and a future
`te.broker.openalgo_orders.OpenAlgoBroker` (real money, later phase) can both
satisfy it without a shared base class.

**Risk R1 resolution** (see the accompanying report for full detail): OpenAlgo's
documented REST API (`.agents/skills/openalgo/references/order-management.md`,
`order-information.md`, `account-services.md`) does not echo a client-supplied
order tag anywhere queryable — `placeorder`'s `strategy` field is stored on the
broker's order book for display/analyzer purposes only, and `orderstatus`
requires the broker's own `orderid` (only obtained from a successful
`placeorder` response). If that response is lost (timeout/dropped ack), there
is no documented way to look the order up by *our* identifier. `query_order()`
below is therefore specified to raise `NotImplementedError` for brokers that
cannot do a client-tag lookup; callers (`te.execution.inflight.resolve_inflight`)
catch that and fall back to matching against `order_reports()` by
`(symbol, side, quantity, submission-timestamp window)` — the plan's own
documented R1 fallback, tractable because the order-management rate bucket is
capped at 5/sec (`te.broker.ratelimit`). This isolates "how do we find out what
happened to an order we lost the ack for" behind one function
(`resolve_inflight`), so wiring a broker that DOES support tag lookup later is
a change to that one function, not to `te.execution.manager`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol

from te.domain.costs import CostBreakdown
from te.domain.events import Side
from te.domain.money import Paise
from te.domain.orders import OrderIntent


@dataclass(frozen=True)
class PlaceOrderAck:
    """What a successful `place_order()` call returns — enough to fold an
    `OrderSubmitted`/`OrderAccepted` pair onto the local event log."""

    client_order_id: str
    venue_order_id: str
    ts: dt.datetime


@dataclass(frozen=True)
class OrderStatusReport:
    """One row of ground truth about an order, from `query_order()` or
    `order_reports()`. `client_order_id` is `None` when the venue has no
    notion of it (e.g. an order placed by hand on the broker's app/website,
    or a fallback-matched row before the local id is attached) — this is
    exactly the shape `te.execution.reconcile.Reconciler` uses to detect
    `EXTERNAL` orders."""

    client_order_id: str | None
    venue_order_id: str
    symbol: str
    exchange: str
    side: Side
    quantity: int
    filled_qty: int
    status: str  # broker-native status string, e.g. "open"/"complete"/"cancelled"/"rejected"
    ts: dt.datetime
    average_price: Paise | None = None


@dataclass(frozen=True)
class FillReport:
    """One fill/trade. `venue_trade_id` is the dedup key —
    `ExecutionManager.on_fill()` ignores a report whose `venue_trade_id` it
    has already applied. `net_amount_paise` is the fill's cash impact
    ALREADY net of `te.domain.costs.CostModel` — the plan's "one fill path"
    cost-net-ness guarantee lives here, not recomputed downstream."""

    client_order_id: str | None
    venue_order_id: str
    venue_trade_id: str
    symbol: str
    exchange: str
    side: Side
    fill_qty: int
    fill_price: Paise
    gross_amount_paise: Paise  # signed: negative (cash out) for BUY, positive (cash in) for SELL
    cost_breakdown: CostBreakdown
    net_amount_paise: Paise  # gross_amount_paise with cost_breakdown.total always subtracted
    ts: dt.datetime


@dataclass(frozen=True)
class PositionReport:
    """Broker's view of one (symbol, exchange) net position — signed
    quantity, positive = long."""

    symbol: str
    exchange: str
    quantity: int


class BrokerPort(Protocol):
    """The only broker abstraction `te.execution` may depend on."""

    def place_order(self, intent: OrderIntent) -> PlaceOrderAck:
        """Submits `intent`. May raise on network failure/timeout — callers
        must NEVER blind-retry this; see `te.execution.manager`."""
        ...

    def cancel_order(self, client_order_id: str, venue_order_id: str) -> None: ...

    def query_order(self, client_order_id: str) -> OrderStatusReport | None:
        """Ground-truth lookup for one order by OUR id. Implementations that
        cannot do this (see the R1 note above) raise `NotImplementedError`
        so callers fall back to `order_reports()` + window matching, rather
        than silently returning `None` (which would be indistinguishable
        from "genuinely not found yet")."""
        ...

    def order_reports(self) -> list[OrderStatusReport]: ...

    def fill_reports(self) -> list[FillReport]: ...

    def position_reports(self) -> list[PositionReport]: ...
