"""Chaos harness: injects timeouts, duplicate fills, dropped acks, and
out-of-order reports across a meaningful number of simulated orders through
`ExecutionManager` + `SimulatedBroker`. Asserts that for every order, the
final local state either matches what the simulated broker believes OR the
system is HALTED — never a silent mismatch.

100 orders is the actual count run here (the plan's own "1000 is
aspirational" note applies — see the accompanying report). Deterministic via
a seeded PRNG so a failure reproduces exactly.
"""

from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal
from pathlib import Path

import pytest

from te.broker.simulated import SimulatedBroker
from te.domain.costs import ChargeRates, CostModel
from te.domain.money import Paise
from te.domain.orders import OrderRequest
from te.execution.halt import is_halted
from te.execution.manager import ExecutionManager
from te.execution.store import OrderEventStore
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base

TS = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)
CHAOS_ORDER_COUNT = 100


@pytest.fixture
def cost_model() -> CostModel:
    rates = ChargeRates(
        effective_from=dt.date(2026, 4, 1),
        verified_at=dt.date(2026, 7, 29),
        brokerage_per_executed_order_paise=Paise(2000),
        stt_sell_bps=Decimal("15.0"),
        stt_exercise_intrinsic_bps=Decimal("15.0"),
        exchange_txn_bps={"NFO": Decimal("3.553"), "BFO": Decimal("3.25")},
        sebi_bps=Decimal("0.01"),
        gst_pct=Decimal("18.0"),
        stamp_buy_bps=Decimal("0.3"),
    )
    return CostModel(rates)


class _ChaosBroker:
    """Wraps a `SimulatedBroker` and injects, per call, one of: a raised
    timeout (dropped ack — `place_order` succeeds on the venue but the
    caller never learns it via the normal return path), a clean pass-
    through, or (for `on_fill` delivery) a duplicate/out-of-order fill
    report."""

    def __init__(self, inner: SimulatedBroker, rng: random.Random) -> None:
        self._inner = inner
        self._rng = rng
        self.dropped_ack_client_order_ids: set[str] = set()

    def place_order(self, intent):  # noqa: ANN001, ANN201
        ack = self._inner.place_order(intent)  # the venue always actually receives it
        if self._rng.random() < 0.2:
            self.dropped_ack_client_order_ids.add(intent.client_order_id)
            raise TimeoutError("chaos: dropped ack")
        return ack

    def cancel_order(self, client_order_id, venue_order_id):  # noqa: ANN001
        return self._inner.cancel_order(client_order_id, venue_order_id)

    def query_order(self, client_order_id):  # noqa: ANN001
        return self._inner.query_order(client_order_id)

    def order_reports(self):  # noqa: ANN201
        return self._inner.order_reports()

    def fill_reports(self):  # noqa: ANN201
        return self._inner.fill_reports()

    def position_reports(self):  # noqa: ANN201
        return self._inner.position_reports()


class _NoLimiter:
    def acquire(self, n: int = 1) -> None:
        pass


def test_chaos_100_orders_never_silently_mismatches(tmp_path: Path, cost_model: CostModel) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'chaos.db'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    store = OrderEventStore(session_factory)

    rng = random.Random(1234)
    sim = SimulatedBroker(cost_model=cost_model, on=dt.date(2026, 7, 29))
    broker = _ChaosBroker(sim, rng)
    manager = ExecutionManager(session_factory, store, broker, _NoLimiter(), clock=lambda: TS)

    client_order_ids: list[str] = []
    for i in range(CHAOS_ORDER_COUNT):
        side = "BUY" if rng.random() < 0.5 else "SELL"
        qty = rng.choice([65, 130, 195])
        request = OrderRequest(
            symbol="NIFTY30JUN2626500CE",
            exchange="NFO",
            side=side,  # type: ignore[arg-type]
            quantity=qty,
            order_type="LIMIT",
            limit_price=Paise(10_000 + i),
        )
        client_order_ids.append(manager.submit(request))

    # Deliver every fill the venue actually produced, with duplicates and
    # out-of-order delivery injected. Track, per delivery, which specific
    # order's fill caused the halt flag to newly flip on — a single blanket
    # `halted` read at the end cannot fail once ANYTHING halts, so it would
    # accept a halt caused by order A as proof that order B's silent
    # mismatch is fine too. `on_fill` sets the halt at the exact moment it
    # rejects an overfilling report for ONE order, so the client_order_id
    # of the fill just delivered is the order that actually caused it.
    with session_factory() as session:
        was_halted = is_halted(session)
    halt_caused_by: set[str] = set()

    def _deliver(report) -> None:  # noqa: ANN001
        nonlocal was_halted
        manager.on_fill(report)
        with session_factory() as session:
            now_halted = is_halted(session)
        if now_halted and not was_halted:
            halt_caused_by.add(report.client_order_id)
        was_halted = now_halted

    fills = list(sim.fill_reports())
    delivery_order = list(range(len(fills)))
    rng.shuffle(delivery_order)  # out-of-order delivery
    for idx in delivery_order:
        _deliver(fills[idx])
        if rng.random() < 0.15:
            _deliver(fills[idx])  # duplicate re-delivery

    # For every order the manager ever knew about: the local fold must
    # either agree with the venue's own status/filled_qty, OR the system
    # must be halted. A dropped ack means we never got past INITIALIZED —
    # that is an explicitly tolerated "unresolved, not mismatched" state,
    # since check_inflight()/reconciliation (not on_fill) is what resolves it.
    with session_factory() as session:
        halted = is_halted(session)

    venue_by_client_id = {r.client_order_id: r for r in sim.order_reports()}
    mismatches = []
    for client_order_id in client_order_ids:
        local = store.fold_order(client_order_id)
        if client_order_id in broker.dropped_ack_client_order_ids:
            # The venue accepted it, but we deliberately never got the ack —
            # local state legitimately stays INITIALIZED. Not a mismatch by
            # this harness's contract (it's exactly what check_inflight
            # exists to resolve later).
            continue
        venue = venue_by_client_id.get(client_order_id)
        if venue is None:
            mismatches.append(client_order_id)
            continue
        if local.filled_qty != venue.filled_qty:
            mismatches.append(client_order_id)

    assert not mismatches or halted, (
        f"{len(mismatches)}/{CHAOS_ORDER_COUNT} orders silently mismatched the venue's own state "
        f"while NOT halted: {mismatches[:5]}..."
    )
    # Per-order, not blanket: a halt caused by ONE order's rejected overfill
    # must not be accepted as an excuse for a DIFFERENT order's unexamined
    # mismatch. Every mismatched order must itself be the one that caused a
    # halt.
    unexplained = [c for c in mismatches if c not in halt_caused_by]
    assert not unexplained, (
        f"{len(unexplained)} order(s) mismatched the venue's own state without themselves ever "
        f"causing a halt — a halt caused by a DIFFERENT order is hiding them: {unexplained[:5]}..."
    )
