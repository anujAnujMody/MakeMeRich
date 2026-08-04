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
    # --- Slippage benchmark, captured at t=0 -----------------------------
    #
    # Added 2026-08-04. `te.risk.monitors.SlippageMonitor` compares an
    # EXPECTED price against the actual fill, and could never run because
    # nothing recorded the expected side: this event held quantity and side
    # but no price at all, and the folded `Order` has no price field either.
    #
    # These live on the INITIALIZED event specifically, not in a side table
    # and not derived afterwards, because they are unrecoverable later: once
    # the fill comes back, the market's state at the instant of submission
    # is gone unless a full tick archive exists. Every framework surveyed
    # (QuantConnect Lean, Freqtrade, Hummingbot) likewise keeps the
    # requested price on the order's creation-time state, and FIX carries
    # the order price on NewOrderSingle itself (tag 44).
    #
    #: What we asked for — the limit price on the request.
    requested_price: Paise | None = None
    #: The market at that instant. The mid of these two, NOT
    #: `requested_price`, is the real benchmark: Perold's implementation
    #: shortfall (1988) measures the fill against the arrival price, so that
    #: a market which had genuinely moved before our order landed is not
    #: scored as bad execution. Bid and ask are stored rather than just the
    #: mid so slippage can later be expressed relative to the spread —
    #: which is how desks actually decide a fill was abnormal — without
    #: replaying tick data.
    arrival_bid: Paise | None = None
    arrival_ask: Paise | None = None


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
