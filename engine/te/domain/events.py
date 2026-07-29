"""Immutable `OrderEvent` union type — the append-only vocabulary Phase 3's
execution core folds into an `Order`. Modelled on NautilusTrader's order
lifecycle: `INITIALIZED -> SUBMITTING/DENIED -> SUBMITTED -> ACCEPTED ->
PARTIALLY_FILLED -> FILLED`, with `CANCELED`/`REJECTED`/`EXPIRED` as other
terminal states. Kept minimal here — full idempotency/reconcile machinery
(client_order_id minting, persist-before-network, dedup by venue_trade_id) is
Phase 3's `te/execution/` package, not this one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from te.domain.money import Paise

Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class OrderInitialized:
    client_order_id: str
    symbol: str
    exchange: str
    side: Side
    quantity: int
    ts: dt.datetime


@dataclass(frozen=True)
class OrderSubmitted:
    client_order_id: str
    ts: dt.datetime


@dataclass(frozen=True)
class OrderDenied:
    """Rejected before ever reaching the venue, e.g. a pre-trade risk check."""

    client_order_id: str
    reason: str
    ts: dt.datetime


@dataclass(frozen=True)
class OrderAccepted:
    client_order_id: str
    venue_order_id: str
    ts: dt.datetime


@dataclass(frozen=True)
class OrderPartiallyFilled:
    client_order_id: str
    venue_trade_id: str
    fill_qty: int
    fill_price: Paise
    ts: dt.datetime


@dataclass(frozen=True)
class OrderFilled:
    client_order_id: str
    venue_trade_id: str
    fill_qty: int
    fill_price: Paise
    ts: dt.datetime


@dataclass(frozen=True)
class OrderCancelled:
    client_order_id: str
    ts: dt.datetime


@dataclass(frozen=True)
class OrderRejected:
    """Rejected by the venue after submission."""

    client_order_id: str
    reason: str
    ts: dt.datetime


@dataclass(frozen=True)
class OrderExpired:
    client_order_id: str
    ts: dt.datetime


OrderEvent = (
    OrderInitialized
    | OrderSubmitted
    | OrderDenied
    | OrderAccepted
    | OrderPartiallyFilled
    | OrderFilled
    | OrderCancelled
    | OrderRejected
    | OrderExpired
)
