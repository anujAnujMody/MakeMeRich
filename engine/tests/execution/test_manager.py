"""te.execution.manager.ExecutionManager — the ordering guarantee
(persist-before-network, never-retry-on-timeout), fill dedup, and the
overfill guard."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.broker.protocol import FillReport, PlaceOrderAck
from te.domain.costs import CostBreakdown
from te.domain.money import Paise
from te.domain.orders import OrderRequest
from te.execution.halt import is_halted
from te.execution.manager import ExecutionManager
from te.execution.store import OrderEventStore
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base

TS = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)
_ZERO_COSTS = CostBreakdown(
    brokerage=Paise(0), stt=Paise(0), exchange_txn=Paise(0), sebi=Paise(0), gst=Paise(0), stamp=Paise(0)
)


def _request(*, side: str = "BUY", qty: int = 65, reduce_only: bool = False) -> OrderRequest:
    return OrderRequest(
        symbol="NIFTY30JUN2626500CE",
        exchange="NFO",
        side=side,
        quantity=qty,
        order_type="LIMIT",
        limit_price=Paise(10_000),  # type: ignore[arg-type]
        reduce_only=reduce_only,
    )


class _RaisingBroker:
    """Raises the instant place_order is called — models both a network
    exception and a broker-side timeout."""

    def __init__(self, exc: type[Exception] = TimeoutError) -> None:
        self.calls = 0
        self._exc = exc

    def place_order(self, intent):  # noqa: ANN001
        self.calls += 1
        raise self._exc("simulated broker timeout")

    def cancel_order(self, client_order_id, venue_order_id):  # noqa: ANN001
        raise NotImplementedError

    def query_order(self, client_order_id):  # noqa: ANN001
        raise NotImplementedError

    def order_reports(self):  # noqa: ANN201
        return []

    def fill_reports(self):  # noqa: ANN201
        return []

    def position_reports(self):  # noqa: ANN201
        return []


class _SucceedingBroker:
    def __init__(self) -> None:
        self.calls = 0

    def place_order(self, intent):  # noqa: ANN001
        self.calls += 1
        return PlaceOrderAck(client_order_id=intent.client_order_id, venue_order_id="v-1", ts=TS)

    def cancel_order(self, client_order_id, venue_order_id):  # noqa: ANN001
        pass

    def query_order(self, client_order_id):  # noqa: ANN001
        return None

    def order_reports(self):  # noqa: ANN201
        return []

    def fill_reports(self):  # noqa: ANN201
        return []

    def position_reports(self):  # noqa: ANN201
        return []


@pytest.fixture
def session_factory(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def store(session_factory) -> OrderEventStore:  # noqa: ANN001
    return OrderEventStore(session_factory)


class _NoLimiter:
    def acquire(self, n: int = 1) -> None:
        pass


def test_client_order_id_persisted_before_network(session_factory, store: OrderEventStore) -> None:  # noqa: ANN001
    broker = _RaisingBroker()
    manager = ExecutionManager(session_factory, store, broker, _NoLimiter(), clock=lambda: TS)

    client_order_id = manager.submit(_request())

    events = store.events_for(client_order_id)
    assert len(events) >= 1
    assert events[0].client_order_id == client_order_id
    assert type(events[0]).__name__ == "OrderInitialized"


def test_timeout_never_retries(session_factory, store: OrderEventStore) -> None:  # noqa: ANN001
    broker = _RaisingBroker()
    manager = ExecutionManager(session_factory, store, broker, _NoLimiter(), clock=lambda: TS)

    client_order_id = manager.submit(_request())

    assert broker.calls == 1
    order = store.fold_order(client_order_id)
    assert order.status == "INITIALIZED"  # never advances past the persisted-before-network mark


def test_successful_submit_reaches_accepted(session_factory, store: OrderEventStore) -> None:  # noqa: ANN001
    broker = _SucceedingBroker()
    manager = ExecutionManager(session_factory, store, broker, _NoLimiter(), clock=lambda: TS)

    client_order_id = manager.submit(_request())

    order = store.fold_order(client_order_id)
    assert order.status == "ACCEPTED"
    assert order.venue_order_id == "v-1"


def test_submit_refuses_when_halted(session_factory, store: OrderEventStore) -> None:  # noqa: ANN001
    from te.execution.halt import set_halt

    with session_factory() as session:
        set_halt(session, "test halt")
        session.commit()

    broker = _SucceedingBroker()
    manager = ExecutionManager(session_factory, store, broker, _NoLimiter(), clock=lambda: TS)

    with pytest.raises(Exception, match="halt"):
        manager.submit(_request())
    assert broker.calls == 0


def test_submit_still_places_reduce_only_orders_when_halted(session_factory, store: OrderEventStore) -> None:  # noqa: ANN001
    """A halt must block new exposure but never block reducing it —
    `reduce_only=True` is the caller's explicit declaration of that (set by
    `te.engine.cycle._close_position` on every exit/square-off SELL).
    Regression test for a live bug: the halt used to block every SELL
    unconditionally, stranding open positions with no working
    stop-loss/trailing-stop/time-exit for the rest of the halt."""
    from te.execution.halt import set_halt

    with session_factory() as session:
        set_halt(session, "test halt")
        session.commit()

    broker = _SucceedingBroker()
    manager = ExecutionManager(session_factory, store, broker, _NoLimiter(), clock=lambda: TS)

    client_order_id = manager.submit(_request(side="SELL", reduce_only=True))

    assert broker.calls == 1
    order = store.fold_order(client_order_id)
    assert order.status == "ACCEPTED"


def test_submit_still_blocks_a_sell_that_is_not_marked_reduce_only(session_factory, store: OrderEventStore) -> None:  # noqa: ANN001
    """The halt bypass is driven by the explicit `reduce_only` flag, not by
    `side` alone — a SELL that doesn't declare itself reduce-only is still
    blocked. Guards against silently reintroducing the side-based inference
    this flag replaced."""
    from te.execution.halt import set_halt

    with session_factory() as session:
        set_halt(session, "test halt")
        session.commit()

    broker = _SucceedingBroker()
    manager = ExecutionManager(session_factory, store, broker, _NoLimiter(), clock=lambda: TS)

    with pytest.raises(Exception, match="halt"):
        manager.submit(_request(side="SELL", reduce_only=False))
    assert broker.calls == 0


def _accepted_order(session_factory, store: OrderEventStore, *, qty: int = 65):  # noqa: ANN001, ANN201
    broker = _SucceedingBroker()
    manager = ExecutionManager(session_factory, store, broker, _NoLimiter(), clock=lambda: TS)
    client_order_id = manager.submit(_request(qty=qty))
    return manager, client_order_id


def _fill(client_order_id: str, *, trade_id: str, qty: int, price: int = 10_000) -> FillReport:
    return FillReport(
        client_order_id=client_order_id,
        venue_order_id="v-1",
        venue_trade_id=trade_id,
        symbol="NIFTY30JUN2626500CE",
        exchange="NFO",
        side="BUY",
        fill_qty=qty,
        fill_price=Paise(price),
        gross_amount_paise=Paise(-price * qty),
        cost_breakdown=_ZERO_COSTS,
        net_amount_paise=Paise(-price * qty),
        ts=TS,
    )


def test_duplicate_fill_ignored(session_factory, store: OrderEventStore) -> None:  # noqa: ANN001
    manager, client_order_id = _accepted_order(session_factory, store)

    manager.on_fill(_fill(client_order_id, trade_id="t-1", qty=65))
    manager.on_fill(_fill(client_order_id, trade_id="t-1", qty=65))  # duplicate

    order = store.fold_order(client_order_id)
    assert order.filled_qty == 65
    assert order.status == "FILLED"
    fill_events = [
        e for e in store.events_for(client_order_id) if type(e).__name__ in ("OrderFilled", "OrderPartiallyFilled")
    ]
    assert len(fill_events) == 1


def test_overfill_rejected_and_halts(session_factory, store: OrderEventStore) -> None:  # noqa: ANN001
    manager, client_order_id = _accepted_order(session_factory, store, qty=65)

    manager.on_fill(_fill(client_order_id, trade_id="t-1", qty=65))
    manager.on_fill(_fill(client_order_id, trade_id="t-2", qty=10))  # would overfill

    order = store.fold_order(client_order_id)
    assert order.filled_qty == 65  # the overfilling fill was rejected, not applied

    with session_factory() as session:
        assert is_halted(session) is True
