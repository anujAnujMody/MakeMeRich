"""Direct unit coverage of `ExecutionManager.check_inflight` — currently
callable directly (not yet wired into the scheduler, see its own docstring)
but a real, reachable code path with no existing test anywhere in the suite
before this file. Uses a minimal fake broker whose `query_order` returns a
caller-controlled `OrderStatusReport | None`, bypassing the
`match_pending_order` fallback machinery (covered separately in
`tests/execution/test_inflight.py`) to isolate `check_inflight`'s own
branching.
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from te.broker.protocol import OrderStatusReport
from te.domain.events import OrderAccepted
from te.domain.money import Paise
from te.domain.orders import OrderRequest, fold
from te.execution.idempotency import persist_initial_event
from te.execution.manager import ExecutionManager
from te.execution.store import OrderEventStore
from te.persistence.models import Base

TS = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)


class _NoLimiter:
    def acquire(self, n: int = 1) -> None:
        pass


class _FakeInflightBroker:
    """`query_order` returns whatever `self.report` is set to (a real
    `OrderStatusReport`, or `None` for "still unresolved"). Never raises
    `NotImplementedError`, so `resolve_inflight`'s fallback path is never
    exercised here."""

    def __init__(self, report: OrderStatusReport | None) -> None:
        self.report = report
        self.query_order_calls = 0

    def query_order(self, client_order_id: str):  # noqa: ANN001, ANN201, ARG002
        self.query_order_calls += 1
        return self.report

    def place_order(self, intent):  # noqa: ANN001, ANN201
        raise NotImplementedError

    def cancel_order(self, client_order_id, venue_order_id):  # noqa: ANN001, ANN201
        raise NotImplementedError

    def order_reports(self):  # noqa: ANN201
        return []

    def fill_reports(self):  # noqa: ANN201
        return []

    def position_reports(self):  # noqa: ANN201
        return []


def _manager(tmp_path, broker):  # noqa: ANN001, ANN201
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'inflight.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    store = OrderEventStore(factory)
    manager = ExecutionManager(factory, store, broker, _NoLimiter(), clock=lambda: TS)
    return manager, store


def _seed_initialized_order(store, *, client_order_id: str) -> None:  # noqa: ANN001
    request = OrderRequest(
        symbol="NIFTY30JUN2626500CE",
        exchange="NFO",
        side="BUY",
        quantity=65,
        order_type="LIMIT",
        limit_price=Paise(7_400),
    )
    persist_initial_event(store, client_order_id=client_order_id, request=request, ts=TS)


def test_check_inflight_skips_an_order_already_past_initialized(tmp_path) -> None:  # noqa: ANN001
    """`order.status != "INITIALIZED"` must skip resolution for an order
    already advanced past the persisted-before-network mark — an `!=` ->
    `==` flip would instead skip every genuinely-stuck INITIALIZED order
    and (wrongly) try to re-resolve every already-resolved one."""
    broker = _FakeInflightBroker(report=None)
    manager, store = _manager(tmp_path, broker)
    client_order_id = "c-accepted"
    _seed_initialized_order(store, client_order_id=client_order_id)
    store.append(OrderAccepted(client_order_id=client_order_id, venue_order_id="v-1", ts=TS))

    manager.check_inflight(client_order_id)

    assert broker.query_order_calls == 0
    assert len(store.events_for(client_order_id)) == 2  # unchanged: Initialized + Accepted only


def test_check_inflight_still_unresolved_leaves_the_order_untouched(tmp_path) -> None:  # noqa: ANN001
    """`report is None` (still unresolved) must return without appending
    anything or raising — an `is` -> `is not` flip would instead skip the
    early return on `None` and crash on `report.status` (`None` has no
    `.status`)."""
    broker = _FakeInflightBroker(report=None)
    manager, store = _manager(tmp_path, broker)
    client_order_id = "c-unresolved"
    _seed_initialized_order(store, client_order_id=client_order_id)

    manager.check_inflight(client_order_id)  # must not raise

    assert len(store.events_for(client_order_id)) == 1  # unchanged: Initialized only


def test_check_inflight_complete_report_appends_accepted_and_filled(tmp_path) -> None:  # noqa: ANN001
    """`report.status == "complete"` must append `OrderAccepted` +
    `OrderFilled` with the reported fill quantity/price — an `==` -> `!=`
    flip would skip this branch entirely (since the status genuinely IS
    "complete") and fall through to the generic `else` branch, silently
    losing the fill."""
    broker = _FakeInflightBroker(
        report=OrderStatusReport(
            client_order_id="c-complete",
            venue_order_id="v-1",
            symbol="NIFTY30JUN2626500CE",
            exchange="NFO",
            side="BUY",
            quantity=65,
            filled_qty=65,
            status="complete",
            ts=TS,
            average_price=Paise(7_450),
        )
    )
    manager, store = _manager(tmp_path, broker)
    client_order_id = "c-complete"
    _seed_initialized_order(store, client_order_id=client_order_id)

    manager.check_inflight(client_order_id)

    order = fold(store.events_for(client_order_id))
    assert order.status == "FILLED"
    assert order.filled_qty == 65


def test_check_inflight_complete_report_with_no_average_price_falls_back_to_zero(tmp_path) -> None:  # noqa: ANN001
    """`report.average_price or Paise(0)` must fall back to `Paise(0)` when
    the broker's resolved report carries no average price — an `or` -> `and`
    flip would instead propagate `None` as the fill price whenever
    `average_price is None` (falsy `and` short-circuits to `None`, not to
    the fallback)."""
    broker = _FakeInflightBroker(
        report=OrderStatusReport(
            client_order_id="c-noprice",
            venue_order_id="v-1",
            symbol="NIFTY30JUN2626500CE",
            exchange="NFO",
            side="BUY",
            quantity=65,
            filled_qty=65,
            status="complete",
            ts=TS,
            average_price=None,
        )
    )
    manager, store = _manager(tmp_path, broker)
    client_order_id = "c-noprice"
    _seed_initialized_order(store, client_order_id=client_order_id)

    manager.check_inflight(client_order_id)

    events = store.events_for(client_order_id)
    filled = [e for e in events if type(e).__name__ == "OrderFilled"]
    assert len(filled) == 1
    assert filled[0].fill_price == Paise(0)


def test_check_inflight_rejected_report_appends_rejected(tmp_path) -> None:  # noqa: ANN001
    """`report.status == "rejected"` must append `OrderRejected` — an `==`
    -> `!=` flip would skip this branch (status genuinely IS "rejected")
    and fall through to the generic `else`, wrongly marking a rejected
    order as accepted."""
    broker = _FakeInflightBroker(
        report=OrderStatusReport(
            client_order_id="c-rejected",
            venue_order_id="v-1",
            symbol="NIFTY30JUN2626500CE",
            exchange="NFO",
            side="BUY",
            quantity=65,
            filled_qty=0,
            status="rejected",
            ts=TS,
        )
    )
    manager, store = _manager(tmp_path, broker)
    client_order_id = "c-rejected"
    _seed_initialized_order(store, client_order_id=client_order_id)

    manager.check_inflight(client_order_id)

    order = fold(store.events_for(client_order_id))
    assert order.status == "REJECTED"


def test_check_inflight_cancelled_report_appends_cancelled(tmp_path) -> None:  # noqa: ANN001
    """`report.status == "cancelled"` must append `OrderCancelled` — an
    `==` -> `!=` flip would skip this branch (status genuinely IS
    "cancelled") and fall through to the generic `else`, wrongly marking a
    cancelled order as accepted."""
    broker = _FakeInflightBroker(
        report=OrderStatusReport(
            client_order_id="c-cancelled",
            venue_order_id="v-1",
            symbol="NIFTY30JUN2626500CE",
            exchange="NFO",
            side="BUY",
            quantity=65,
            filled_qty=0,
            status="cancelled",
            ts=TS,
        )
    )
    manager, store = _manager(tmp_path, broker)
    client_order_id = "c-cancelled"
    _seed_initialized_order(store, client_order_id=client_order_id)

    manager.check_inflight(client_order_id)

    order = fold(store.events_for(client_order_id))
    assert order.status == "CANCELED"
