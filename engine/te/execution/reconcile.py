"""`Reconciler` — boot + continuous reconciliation against the broker's own
records. Diffs `BrokerPort.order_reports()`/`position_reports()` against the
locally folded event-sourced state. Per the plan, this NEVER auto-heals:

- An order the venue reports that we have no `client_order_id` for (or whose
  id we don't recognise) is marked `EXTERNAL` and HALTS — it might be a
  manual order placed on the broker's app, a bug, or worse; the engine must
  not guess.
- Any position-quantity disagreement between the broker and the local fold
  HALTS too, for the same reason — see risk R8: reconciliation halts are
  expected to be frequent early on, and resisting the temptation to
  auto-heal is deliberate. `POST /api/engine/acknowledge-reconciliation`
  (a later phase's API surface) is the only sanctioned way past a halt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from te.broker.protocol import BrokerPort, OrderStatusReport
from te.execution.halt import set_halt
from te.execution.store import OrderEventStore
from te.persistence.db import session_scope

ReconcileMode = Literal["boot", "continuous"]


@dataclass(frozen=True)
class ReconcileResult:
    external_orders: list[OrderStatusReport] = field(default_factory=list)
    position_mismatches: list[tuple[str, int, int]] = field(default_factory=list)  # (symbol, local_qty, broker_qty)
    halted: bool = False


class Reconciler:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        store: OrderEventStore,
        broker: BrokerPort,
    ) -> None:
        self._session_factory = session_factory
        self._store = store
        self._broker = broker

    def reconcile(self, mode: ReconcileMode) -> ReconcileResult:
        known_ids = set(self._store.all_client_order_ids())
        order_reports = self._broker.order_reports()
        external = [r for r in order_reports if r.client_order_id is None or r.client_order_id not in known_ids]

        local_positions = self._local_positions()
        broker_positions = {(p.symbol, p.exchange): p.quantity for p in self._broker.position_reports()}
        mismatches: list[tuple[str, int, int]] = []
        for key in set(local_positions) | set(broker_positions):
            local_qty = local_positions.get(key, 0)
            broker_qty = broker_positions.get(key, 0)
            if local_qty != broker_qty:
                mismatches.append((key[0], local_qty, broker_qty))

        halted = bool(external) or bool(mismatches)
        if halted:
            reasons = []
            if external:
                reasons.append(f"{len(external)} external order(s) detected")
            if mismatches:
                reasons.append(f"{len(mismatches)} position mismatch(es)")
            with session_scope(self._session_factory) as session:
                set_halt(session, f"reconcile[{mode}]: " + "; ".join(reasons))

        return ReconcileResult(external_orders=external, position_mismatches=mismatches, halted=halted)

    def _local_positions(self) -> dict[tuple[str, str], int]:
        positions: dict[tuple[str, str], int] = {}
        for client_order_id in self._store.all_client_order_ids():
            order = self._store.fold_order(client_order_id)
            if order.filled_qty == 0:
                continue
            signed = order.filled_qty if order.side == "BUY" else -order.filled_qty
            key = (order.symbol, order.exchange)
            positions[key] = positions.get(key, 0) + signed
        return positions
