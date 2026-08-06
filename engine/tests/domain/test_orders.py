import datetime as dt

import pytest

from te.domain.events import (
    OrderAccepted,
    OrderCancelled,
    OrderDenied,
    OrderEvent,
    OrderFilled,
    OrderInitialized,
    OrderPartiallyFilled,
    OrderRejected,
    OrderSubmitted,
)
from te.domain.money import Paise
from te.domain.orders import Order, fold

TS = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)


def _initialized(qty: int = 65) -> OrderInitialized:
    return OrderInitialized(
        client_order_id="coid-1", symbol="NIFTY30JUN2626500CE", exchange="NFO", side="BUY", quantity=qty, ts=TS
    )


def test_fold_requires_events() -> None:
    with pytest.raises(ValueError, match="at least one event"):
        fold([])


def test_fold_first_event_must_be_initialized() -> None:
    with pytest.raises(ValueError, match="OrderInitialized"):
        fold([OrderSubmitted(client_order_id="coid-1", ts=TS)])


def test_fold_single_initialized_event() -> None:
    order = fold([_initialized()])
    assert order.status == "INITIALIZED"
    assert order.client_order_id == "coid-1"
    assert order.filled_qty == 0


def test_fold_submitted_then_accepted() -> None:
    order = fold(
        [
            _initialized(),
            OrderSubmitted(client_order_id="coid-1", ts=TS),
            OrderAccepted(client_order_id="coid-1", venue_order_id="v-1", ts=TS),
        ]
    )
    assert order.status == "ACCEPTED"
    assert order.venue_order_id == "v-1"


def test_fold_denied() -> None:
    order = fold([_initialized(), OrderDenied(client_order_id="coid-1", reason="risk limit", ts=TS)])
    assert order.status == "DENIED"


def test_fold_partial_fill_then_fill_accumulates_quantity() -> None:
    order = fold(
        [
            _initialized(qty=65),
            OrderSubmitted(client_order_id="coid-1", ts=TS),
            OrderAccepted(client_order_id="coid-1", venue_order_id="v-1", ts=TS),
            OrderPartiallyFilled(
                client_order_id="coid-1", venue_trade_id="t-1", fill_qty=30, fill_price=Paise(10_000), ts=TS
            ),
            OrderFilled(client_order_id="coid-1", venue_trade_id="t-2", fill_qty=35, fill_price=Paise(10_050), ts=TS),
        ]
    )
    assert order.status == "FILLED"
    assert order.filled_qty == 65


def test_fold_rejected() -> None:
    order = fold([_initialized(), OrderRejected(client_order_id="coid-1", reason="bad symbol", ts=TS)])
    assert order.status == "REJECTED"


def test_fold_cancelled() -> None:
    order = fold(
        [
            _initialized(),
            OrderSubmitted(client_order_id="coid-1", ts=TS),
            OrderAccepted(client_order_id="coid-1", venue_order_id="v-1", ts=TS),
            OrderCancelled(client_order_id="coid-1", ts=TS),
        ]
    )
    assert order.status == "CANCELED"


def test_fold_is_pure_and_deterministic() -> None:
    events: list[OrderEvent] = [
        _initialized(),
        OrderSubmitted(client_order_id="coid-1", ts=TS),
        OrderAccepted(client_order_id="coid-1", venue_order_id="v-1", ts=TS),
    ]
    first = fold(events)
    second = fold(events)
    assert first == second
    assert isinstance(first, Order)
