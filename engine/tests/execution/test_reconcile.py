"""te.execution.reconcile.Reconciler — boot + continuous reconciliation.
Unknown orders are marked EXTERNAL and HALT; any position-quantity
disagreement HALTS too — never auto-heals."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.broker.protocol import OrderStatusReport, PositionReport
from te.domain.events import OrderAccepted, OrderFilled, OrderInitialized, OrderSubmitted
from te.domain.money import Paise
from te.execution.halt import is_halted
from te.execution.reconcile import Reconciler
from te.execution.store import OrderEventStore
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base

TS = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)


@pytest.fixture
def session_factory(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def store(session_factory) -> OrderEventStore:  # noqa: ANN001
    return OrderEventStore(session_factory)


class _FakeBroker:
    def __init__(
        self,
        order_reports: list[OrderStatusReport] | None = None,
        position_reports: list[PositionReport] | None = None,
    ) -> None:
        self._order_reports = order_reports or []
        self._position_reports = position_reports or []

    def place_order(self, intent):  # noqa: ANN001, ANN201
        raise NotImplementedError

    def cancel_order(self, client_order_id, venue_order_id):  # noqa: ANN001
        raise NotImplementedError

    def query_order(self, client_order_id):  # noqa: ANN001
        raise NotImplementedError

    def order_reports(self) -> list[OrderStatusReport]:
        return self._order_reports

    def fill_reports(self):  # noqa: ANN201
        return []

    def position_reports(self) -> list[PositionReport]:
        return self._position_reports


def _known_filled_order(store: OrderEventStore, *, client_order_id: str, symbol: str, qty: int) -> None:  # noqa: ANN001
    store.append(
        OrderInitialized(
            client_order_id=client_order_id, symbol=symbol, exchange="NFO", side="BUY", quantity=qty, ts=TS
        )
    )
    store.append(OrderSubmitted(client_order_id=client_order_id, ts=TS))
    store.append(OrderAccepted(client_order_id=client_order_id, venue_order_id="v-1", ts=TS))
    store.append(
        OrderFilled(
            client_order_id=client_order_id, venue_trade_id="t-1", fill_qty=qty, fill_price=Paise(10_000), ts=TS
        )
    )


def test_boot_reconcile_clean_state_does_not_halt(session_factory, store: OrderEventStore) -> None:  # noqa: ANN001
    _known_filled_order(store, client_order_id="coid-1", symbol="NIFTY", qty=65)
    broker = _FakeBroker(
        order_reports=[
            OrderStatusReport(
                client_order_id="coid-1",
                venue_order_id="v-1",
                symbol="NIFTY",
                exchange="NFO",
                side="BUY",
                quantity=65,
                filled_qty=65,
                status="complete",
                ts=TS,
            )
        ],
        position_reports=[PositionReport(symbol="NIFTY", exchange="NFO", quantity=65)],
    )
    reconciler = Reconciler(session_factory, store, broker)

    result = reconciler.reconcile("boot")

    assert result.halted is False
    with session_factory() as session:
        assert is_halted(session) is False


def test_boot_reconcile_detects_external_order_and_halts(session_factory, store: OrderEventStore) -> None:  # noqa: ANN001
    broker = _FakeBroker(
        order_reports=[
            OrderStatusReport(
                client_order_id=None,
                venue_order_id="v-external",
                symbol="NIFTY",
                exchange="NFO",
                side="BUY",
                quantity=65,
                filled_qty=65,
                status="complete",
                ts=TS,
            )
        ],
    )
    reconciler = Reconciler(session_factory, store, broker)

    result = reconciler.reconcile("boot")

    assert result.halted is True
    assert len(result.external_orders) == 1
    with session_factory() as session:
        assert is_halted(session) is True


def test_boot_reconcile_detects_position_mismatch_and_halts(session_factory, store: OrderEventStore) -> None:  # noqa: ANN001
    _known_filled_order(store, client_order_id="coid-1", symbol="NIFTY", qty=65)
    broker = _FakeBroker(
        order_reports=[
            OrderStatusReport(
                client_order_id="coid-1",
                venue_order_id="v-1",
                symbol="NIFTY",
                exchange="NFO",
                side="BUY",
                quantity=65,
                filled_qty=65,
                status="complete",
                ts=TS,
            )
        ],
        position_reports=[PositionReport(symbol="NIFTY", exchange="NFO", quantity=130)],  # broker disagrees
    )
    reconciler = Reconciler(session_factory, store, broker)

    result = reconciler.reconcile("boot")

    assert result.halted is True
    assert len(result.position_mismatches) == 1
    with session_factory() as session:
        assert is_halted(session) is True


def test_a_broker_position_the_engine_never_heard_of_halts(session_factory, store: OrderEventStore) -> None:  # noqa: ANN001
    """The single case this module exists for, per its own docstring: "a
    position the broker thinks we hold". An empty local store against a
    broker-reported position — the local side of the union is empty, so this
    only reconciles at all if the union includes the BROKER's keys too."""
    broker = _FakeBroker(
        position_reports=[PositionReport(symbol="NIFTY", exchange="NFO", quantity=65)],
    )
    reconciler = Reconciler(session_factory, store, broker)

    result = reconciler.reconcile("boot")

    assert result.halted is True
    assert result.position_mismatches == [("NIFTY", 0, 65)]
    with session_factory() as session:
        assert is_halted(session) is True


def test_a_locally_filled_order_the_broker_does_not_report_halts(session_factory, store: OrderEventStore) -> None:  # noqa: ANN001
    """The other one-sided case: we hold a locally-filled position and the
    broker reports nothing for it — `position_reports=[]`. This only
    reconciles at all if the union includes the LOCAL side's keys too."""
    _known_filled_order(store, client_order_id="coid-1", symbol="NIFTY", qty=65)
    broker = _FakeBroker(position_reports=[])
    reconciler = Reconciler(session_factory, store, broker)

    result = reconciler.reconcile("boot")

    assert result.halted is True
    assert result.position_mismatches == [("NIFTY", 65, 0)]
    with session_factory() as session:
        assert is_halted(session) is True


def test_continuous_mode_also_halts(session_factory, store: OrderEventStore) -> None:  # noqa: ANN001
    broker = _FakeBroker(
        order_reports=[
            OrderStatusReport(
                client_order_id=None,
                venue_order_id="v-external",
                symbol="NIFTY",
                exchange="NFO",
                side="BUY",
                quantity=65,
                filled_qty=65,
                status="complete",
                ts=TS,
            )
        ],
    )
    reconciler = Reconciler(session_factory, store, broker)
    result = reconciler.reconcile("continuous")
    assert result.halted is True
