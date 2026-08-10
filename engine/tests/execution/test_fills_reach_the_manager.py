"""The fill half of the order lifecycle was never connected.

`ExecutionManager.on_fill` was written, tested, and correct: it dedups by
`venue_trade_id`, enforces the overfill guard, and calls `_observe_slippage`.
`BrokerPort.fill_reports()` was declared on the protocol and implemented by
both brokers. Nothing in `te/` ever joined the two. `grep -rn "on_fill" te/`
returned only its own definition and two comments; every other hit was a test
calling it by hand.

So in production the lifecycle stopped at `ACCEPTED`:

* `fold()` reported `filled_qty=0`, so every paper position rendered
  perpetually OPEN with nothing filled — a position the engine had really
  bought, displayed as an order still working.
* `_observe_slippage` never ran once, so `slippage_observations` stayed
  empty and `te.risk.live_gate`'s "Tier-0 slippage is clean" condition
  passed having measured nothing (fixed alongside this — see
  `tests/risk/test_live_gate.py`).

These tests pin the join itself, not the pieces: after a real `submit()`
against a real `SimulatedBroker`, the order must be FILLED and the
observation must exist. Both assertions fail against the unfixed code.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.broker.simulated import SimulatedBroker
from te.broker.ratelimit import TokenBucket
from te.data.charges_loader import load_charge_rate_table
from te.domain.costs import CostModel, select_rates
from te.domain.money import Paise
from te.domain.orders import OrderRequest, fold
from te.execution.manager import ExecutionManager
from te.execution.store import OrderEventStore
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base
from te.persistence.repos.monitors import recent_slippage_observations

ON = dt.date(2026, 8, 6)


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'fills.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def stack(session_factory):  # noqa: ANN001, ANN201
    """A real paper stack — the same shape `build_paper_execution_stack`
    assembles for the live paper cycle, not a hand-rolled fake. The point of
    these tests is that the REAL wiring carries fills."""
    cost_model = CostModel(select_rates(load_charge_rate_table(Path("config/charges.yaml")), ON))
    broker = SimulatedBroker(cost_model=cost_model, on=ON)
    store = OrderEventStore(session_factory)
    manager = ExecutionManager(session_factory, store, broker, TokenBucket(rate=10.0, capacity=10))
    return manager, store, broker


def _request(*, side: str = "BUY", qty: int = 65) -> OrderRequest:
    return OrderRequest(
        symbol="NIFTY06AUG2624600CE",
        exchange="NFO",
        side=side,
        quantity=qty,
        order_type="LIMIT",
        limit_price=Paise(16_680),  # type: ignore[arg-type]
        arrival_bid=Paise(16_600),  # type: ignore[arg-type]
        arrival_ask=Paise(16_680),  # type: ignore[arg-type]
    )


def test_submitting_an_order_leaves_it_filled_not_perpetually_open(stack) -> None:  # noqa: ANN001
    manager, store, _ = stack

    client_order_id = manager.submit(_request())

    order = fold(store.events_for(client_order_id))
    assert order.filled_qty == 65, (
        "the broker filled this order in full; a position the engine really "
        "bought must not read as still working"
    )
    assert order.status == "FILLED"


def test_a_fill_records_a_slippage_observation(stack, session_factory) -> None:  # noqa: ANN001
    manager, _, _ = stack

    manager.submit(_request())

    with session_factory() as session:
        rows = recent_slippage_observations(session, instrument="NIFTY06AUG2624600CE", limit=50)
    assert len(rows) == 1, "the Tier-0 monitor must actually observe production fills"


def test_draining_twice_does_not_double_count(stack, session_factory) -> None:  # noqa: ANN001
    """`drain_fills` is called on every `submit`, so a second order in the
    same cycle re-reads the first order's fill from the broker. `on_fill`
    dedups by `venue_trade_id`, and this pins that the dedup covers the
    drain path too — otherwise the overfill guard would halt the engine on
    the second submit of every session."""
    manager, store, _ = stack

    first = manager.submit(_request())
    manager.drain_fills()
    manager.drain_fills()

    order = fold(store.events_for(first))
    assert order.filled_qty == 65, "re-draining must not re-apply a fill already recorded"

    from te.execution.halt import is_halted

    with session_factory() as session:
        assert is_halted(session) is False, "a re-read fill must not trip the overfill guard"


def test_a_second_order_does_not_halt_on_the_first_orders_fill(stack, session_factory) -> None:  # noqa: ANN001
    manager, store, _ = stack

    first = manager.submit(_request())
    second = manager.submit(_request(qty=65))

    from te.execution.halt import is_halted

    with session_factory() as session:
        assert is_halted(session) is False
    assert fold(store.events_for(first)).filled_qty == 65
    assert fold(store.events_for(second)).filled_qty == 65
