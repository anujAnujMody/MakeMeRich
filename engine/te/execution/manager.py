"""`ExecutionManager` — the order lifecycle that cannot double-fire,
overfill, or silently diverge from the broker.

Ordering (copied from NautilusTrader, this is the whole point of Phase 3):
`client_order_id` is minted and PERSISTED to `order_events` *before* the
broker is ever called. On any broker exception (including a timeout) the
order is left exactly where it was persisted (`INITIALIZED`) — an in-flight
timer/periodic check (`check_inflight`, backed by `te.execution.inflight.
resolve_inflight`) is what later learns the ground truth; `submit()` itself
NEVER retries the network call.

`on_fill()` dedups by `venue_trade_id` and enforces the overfill guard:
any fill where `filled_qty + last_qty > order.quantity` is rejected AND the
system halts (`te.execution.halt`) rather than silently dropping the fill or
raising and losing the state.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from te.broker.protocol import BrokerPort, FillReport
from te.broker.ratelimit import TokenBucket
from te.broker.simulated import SimulatedBroker
from te.domain.costs import ChargeRateTable, CostModel, select_rates
from te.domain.events import (
    OrderAccepted,
    OrderCancelled,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRejected,
    OrderSubmitted,
)
from te.domain.money import Paise
from te.domain.orders import OrderIntent, OrderRequest, fold
from te.execution.halt import is_halted, set_halt
from te.execution.idempotency import mint_client_order_id, persist_initial_event
from te.execution.inflight import resolve_inflight
from te.execution.store import OrderEventStore
from te.persistence.db import session_scope

_TERMINAL_FILL_EVENTS = (OrderFilled, OrderPartiallyFilled)


class HaltedError(RuntimeError):
    """Raised by `submit()` when the halt flag is set — execution refuses to
    place any new order until a human explicitly acknowledges/clears it."""


class RateLimiter(Protocol):
    def acquire(self, n: int = 1) -> None: ...


class ExecutionManager:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        store: OrderEventStore,
        broker: BrokerPort,
        rate_limiter: RateLimiter,
        *,
        clock: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._store = store
        self._broker = broker
        self._rate_limiter = rate_limiter
        self._clock: Callable[[], dt.datetime] = clock or (lambda: dt.datetime.now(dt.UTC))

    def submit(self, request: OrderRequest) -> str:
        # A halt blocks new exposure but never blocks reducing it —
        # `request.reduce_only` is the caller's explicit declaration of
        # which this order is (see `OrderRequest.reduce_only`'s docstring).
        # Found live: the halt used to block every SELL unconditionally,
        # which meant tripping the daily-loss kill switch — the exact event
        # meant to protect capital — would strand every open position with
        # no stop-loss, trailing stop, or time exit for the rest of the
        # halt, contradicting this project's non-negotiable "every position
        # always has a working exit plan" rule.
        if not request.reduce_only:
            with self._session_factory() as session:
                if is_halted(session):
                    raise HaltedError("execution is halted — refusing to submit new orders")

        client_order_id = mint_client_order_id()
        ts = self._clock()

        # 1. PERSIST before any network call — the whole point of Phase 3.
        persist_initial_event(self._store, client_order_id=client_order_id, request=request, ts=ts)

        # 2. Rate-limit, then call the venue. NEVER retry on failure here —
        #    a timeout leaves the order exactly at INITIALIZED; only
        #    check_inflight()/reconciliation may advance it from there.
        self._rate_limiter.acquire()
        intent = OrderIntent(
            client_order_id=client_order_id,
            symbol=request.symbol,
            exchange=request.exchange,
            side=request.side,
            quantity=request.quantity,
            order_type=request.order_type,
            limit_price=request.limit_price,
            ts=ts,
        )
        try:
            ack = self._broker.place_order(intent)
        except Exception:
            return client_order_id

        self._store.append(OrderSubmitted(client_order_id=client_order_id, ts=self._clock()))
        self._store.append(OrderAccepted(client_order_id=client_order_id, venue_order_id=ack.venue_order_id, ts=ack.ts))
        return client_order_id

    def on_fill(self, report: FillReport) -> None:
        """Applies one fill, deduped by `venue_trade_id`, guarded against
        overfill. A fill with no `client_order_id` is not this manager's
        job — `te.execution.reconcile.Reconciler` is what surfaces
        unattributed fills as `EXTERNAL`."""
        if report.client_order_id is None:
            return

        events = self._store.events_for(report.client_order_id)
        if not events:
            return  # unknown order — reconciliation's job, not on_fill's

        for event in events:
            if isinstance(event, _TERMINAL_FILL_EVENTS) and event.venue_trade_id == report.venue_trade_id:
                return  # duplicate — already applied

        order = fold(events)
        prospective_total = order.filled_qty + report.fill_qty
        if prospective_total > order.quantity:
            with session_scope(self._session_factory) as session:
                set_halt(
                    session,
                    f"overfill rejected on {report.client_order_id}: "
                    f"{order.filled_qty} + {report.fill_qty} > {order.quantity}",
                )
            return

        event_cls = OrderFilled if prospective_total == order.quantity else OrderPartiallyFilled
        self._store.append(
            event_cls(
                client_order_id=report.client_order_id,
                venue_trade_id=report.venue_trade_id,
                fill_qty=report.fill_qty,
                fill_price=report.fill_price,
                ts=report.ts,
            )
        )

    def check_inflight(self, client_order_id: str, *, window: dt.timedelta = dt.timedelta(seconds=5)) -> None:
        """Periodic in-flight resolution — TODO(Phase 4): wire this into
        `te/engine/scheduler.py` as a recurring job once that scheduler owns
        engine-cycle jobs generally; for now it's callable directly (e.g.
        from a manual/ops script or a test) per this phase's stated scope
        ("stub the periodic scheduling")."""
        events = self._store.events_for(client_order_id)
        if not events:
            return
        order = fold(events)
        if order.status != "INITIALIZED":
            return  # already resolved past the persisted-before-network mark

        submitted_at = events[0].ts
        report = resolve_inflight(order, submitted_at, self._broker, window=window)
        if report is None:
            return  # still unresolved — a later call will try again

        if report.status == "complete":
            self._store.append(
                OrderAccepted(client_order_id=client_order_id, venue_order_id=report.venue_order_id, ts=report.ts)
            )
            self._store.append(
                OrderFilled(
                    client_order_id=client_order_id,
                    venue_trade_id=f"{report.venue_order_id}-resolved",
                    fill_qty=report.filled_qty,
                    fill_price=report.average_price or Paise(0),
                    ts=report.ts,
                )
            )
        elif report.status == "rejected":
            self._store.append(
                OrderRejected(
                    client_order_id=client_order_id, reason="resolved rejected via inflight query", ts=report.ts
                )
            )
        elif report.status == "cancelled":
            self._store.append(OrderCancelled(client_order_id=client_order_id, ts=report.ts))
        else:
            self._store.append(
                OrderAccepted(client_order_id=client_order_id, venue_order_id=report.venue_order_id, ts=report.ts)
            )


def build_paper_execution_stack(
    session_factory: sessionmaker[Session],
    *,
    charge_rate_table: ChargeRateTable,
    max_orders_per_second: float,
    on: dt.date,
) -> tuple[CostModel, ExecutionManager]:
    """The `CostModel` -> `SimulatedBroker` -> `TokenBucket` ->
    `ExecutionManager` stack every paper-trading entry point needs — the
    scheduled cycle (`te.engine.scheduler.PaperCycleRunner.run_once`) and
    the manual square-off API route (`te.api.routers.positions`) used to
    each hand-assemble this identically."""
    cost_model = CostModel(select_rates(charge_rate_table, on))
    broker = SimulatedBroker(cost_model=cost_model, on=on)
    rate_limiter = TokenBucket(rate=max_orders_per_second, capacity=int(max_orders_per_second))
    execution = ExecutionManager(session_factory, OrderEventStore(session_factory), broker, rate_limiter)
    return cost_model, execution
