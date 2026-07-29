import datetime as dt

from fastapi import APIRouter, Response

from te.api.provenance import set_provenance
from te.api.schemas.trading import CancelOrderPayload, Order, PlaceOrderPayload, RejectedOrder, SuccessResponse
from te.api.state import state

# Prefix is only `/api`: this router serves both `/api/orders*` and
# `/api/rejected-orders`, which share no deeper segment, and the resolved
# URLs are a frozen contract.
router = APIRouter(prefix="/api", tags=["orders"])


@router.get("/orders", response_model=list[Order])
def list_orders(response: Response) -> list[Order]:
    """Today's orders, newest first. Only ever contains orders recorded by
    this process — the execution core is not wired up, so no real broker
    orders exist."""
    set_provenance(response, not_ready_reason="phase-0: execution core not wired yet, zero real orders")
    return state.orders


@router.post("/orders/place", response_model=Order, status_code=201)
def place_order(payload: PlaceOrderPayload, response: Response) -> Order:
    """Records an order and returns it as REJECTED.

    Phase 0 records the order shape honestly (nothing is actually sent to
    a broker — `broker/simulated.py` doesn't exist until Phase 3)."""
    set_provenance(response, not_ready_reason="phase-0: execution core not wired yet, order not actually placed")
    now = dt.datetime.now(dt.UTC).isoformat()
    order = Order(
        id=f"o-{len(state.orders) + 1}",
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
    state.orders.insert(0, order)
    return order


@router.post("/orders/cancel", response_model=SuccessResponse)
def cancel_order(payload: CancelOrderPayload, response: Response) -> SuccessResponse:
    """Marks a locally-recorded order CANCELLED. Nothing reaches a broker —
    the execution core is not wired up, so this only mutates in-memory
    Phase-0 state."""
    set_provenance(response, not_ready_reason="phase-0: execution core not wired yet")
    for i, order in enumerate(state.orders):
        if order.id == payload.id:
            state.orders[i] = order.model_copy(update={"status": "CANCELLED"})
    return SuccessResponse(success=True)


@router.get("/rejected-orders", response_model=list[RejectedOrder])
def list_rejected_orders(response: Response) -> list[RejectedOrder]:
    """Orders the broker rejected, with the rejection reason. Empty until the
    execution core is wired up."""
    set_provenance(response, not_ready_reason="phase-0: execution core not wired yet")
    return []
