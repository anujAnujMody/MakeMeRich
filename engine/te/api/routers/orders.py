import datetime as dt

from fastapi import APIRouter, Response

from te.api.db import session_factory
from te.api.provenance import set_provenance
from te.api.schemas.trading import (
    CancelOrderPayload,
    Order,
    PlaceOrderPayload,
    RejectedOrder,
    SuccessResponse,
)
from te.api.schemas.trading import (
    OrderStatus as ApiOrderStatus,
)
from te.domain.clock import IST
from te.domain.events import OrderFilled, OrderPartiallyFilled
from te.domain.money import Paise, rupees
from te.domain.orders import OrderStatus as DomainOrderStatus
from te.execution.store import OrderEventStore
from te.persistence.repos.paper_trading import order_ids_today

# Prefix is only `/api`: this router serves both `/api/orders*` and
# `/api/rejected-orders`, which share no deeper segment, and the resolved
# URLs are a frozen contract.
router = APIRouter(prefix="/api", tags=["orders"])

#: Domain `OrderStatus` (te.domain.orders) has more states than the
#: dashboard contract's `OrderStatus` — collapse honestly rather than force
#: a 1:1 mapping that doesn't exist. DENIED/REJECTED/EXPIRED all map to
#: REJECTED (closest available bucket; the real reason, when there is one,
#: still exists in the event log even though this list view doesn't surface
#: it — `/api/rejected-orders` is the dedicated view for that).
_STATUS_MAP: dict[DomainOrderStatus, ApiOrderStatus] = {
    "INITIALIZED": "PENDING",
    "SUBMITTED": "PENDING",
    "DENIED": "REJECTED",
    "ACCEPTED": "OPEN",
    "PARTIALLY_FILLED": "OPEN",
    "FILLED": "COMPLETE",
    "CANCELED": "CANCELLED",
    "REJECTED": "REJECTED",
    "EXPIRED": "REJECTED",
}


@router.get("/orders", response_model=list[Order])
def list_orders(response: Response) -> list[Order]:
    """Today's orders, newest first, folded from the real append-only
    `order_events` log (Phase 3's execution core) — the execution core
    landed in Phase 3; this router was simply never repointed at it
    afterwards.

    `price`/`triggerPrice`/`orderType`/`productType` have no backing field in
    this event log — every order this engine places is an intraday MARKET
    order (see `te.domain.orders.OrderRequest`'s default and
    `te.broker.simulated.SimulatedBroker`, which has no limit-order concept),
    so those are honest constants for THIS engine, not fabricated values;
    `price`/`triggerPrice` stay `0` since a market order has no limit price
    to report. `strategy` is `None` — order events don't carry a strategy
    tag today."""
    store = OrderEventStore(session_factory)
    with session_factory() as session:
        today = dt.datetime.now(IST).date()
        ids = order_ids_today(session, today)

    orders: list[Order] = []
    for client_order_id in ids:
        events = store.events_for(client_order_id)
        if not events:
            continue
        folded = store.fold_order(client_order_id)
        fills = [e for e in events if isinstance(e, OrderFilled | OrderPartiallyFilled)]
        avg_price = (
            float(rupees(Paise(sum(f.fill_price * f.fill_qty for f in fills) // sum(f.fill_qty for f in fills))))
            if fills
            else 0.0
        )
        orders.append(
            Order(
                id=client_order_id,
                symbol=folded.symbol,
                exchange=folded.exchange,
                transactionType=folded.side,
                quantity=folded.quantity,
                price=0.0,
                triggerPrice=0.0,
                status=_STATUS_MAP[folded.status],
                orderType="MARKET",
                productType="MIS",
                filledQty=folded.filled_qty,
                averagePrice=avg_price,
                createdAt=events[0].ts.isoformat(),
                updatedAt=events[-1].ts.isoformat(),
                strategy=None,
            )
        )
    orders.sort(key=lambda o: o.createdAt, reverse=True)
    set_provenance(response, provenance="paper" if orders else "none", sample_size=len(orders))
    return orders


@router.post("/orders/place", response_model=Order, status_code=201)
def place_order(payload: PlaceOrderPayload, response: Response) -> Order:
    """Manual order placement from the dashboard is not wired to the real
    execution core (`ExecutionManager`) — only the automated ORB paper cycle
    submits real orders today. Honestly rejects rather than pretending to
    place one; see the plan's Tier-2 approvals decision for why this stays
    unbuilt for now (real order placement is money-adjacent and gated behind
    a dedicated, tested implementation, not rushed alongside this wiring
    pass)."""
    set_provenance(response, not_ready_reason="manual order placement not wired to the execution core yet")
    now = dt.datetime.now(dt.UTC).isoformat()
    return Order(
        id="unplaced",
        symbol=payload.symbol,
        exchange=payload.exchange,
        transactionType=payload.transactionType,
        quantity=payload.quantity,
        price=payload.price,
        triggerPrice=payload.triggerPrice or 0,
        status="REJECTED",
        orderType=payload.orderType,
        productType=payload.productType,
        filledQty=0,
        averagePrice=0,
        createdAt=now,
        updatedAt=now,
    )


@router.post("/orders/cancel", response_model=SuccessResponse)
def cancel_order(payload: CancelOrderPayload, response: Response) -> SuccessResponse:
    """Every order this engine places fills synchronously and completely at
    submission time (`SimulatedBroker.place_order` — see its docstring)
    there is never a pending order to cancel, so this honestly reports
    failure rather than pretending to cancel something that was never in a
    cancellable state. The dashboard hides/disables Cancel for paper orders
    for the same reason (see the plan's Tier-2 section)."""
    set_provenance(response, not_ready_reason="paper orders fill synchronously, nothing is ever cancellable")
    return SuccessResponse(success=False)


@router.get("/rejected-orders", response_model=list[RejectedOrder])
def list_rejected_orders(response: Response) -> list[RejectedOrder]:
    """Orders the risk/execution layer denied or the venue rejected, with the
    reason — from the real `order_events` log's `OrderDenied`/`OrderRejected`
    events, restricted to today. A fast-follow, not wired in this pass; the
    honest-empty default is unchanged from before."""
    set_provenance(response, not_ready_reason="tier-0 fast-follow: rejected-order reasons not surfaced yet")
    return []
