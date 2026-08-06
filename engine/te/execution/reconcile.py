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

### Why this is NOT scheduled, decided 2026-08-04

Deliberately unscheduled, not forgotten. Turning it on while the engine
paper-trades would halt it on the first cycle.

`reconcile()` diffs local open positions against `BrokerPort.position_reports()`.
In paper mode the broker is a `SimulatedBroker` rebuilt FRESH inside
`te.execution.manager.build_paper_execution_stack` every cycle, so its
`_positions` is always empty. Every genuinely open position reads as a
mismatch, and a mismatch halts by design.

NautilusTrader draws the same line for the same reason -- "Only the
`LiveExecutionEngine` performs reconciliation, since backtesting controls both
sides" (https://nautilustrader.io/docs/latest/concepts/reconciliation).
Reconciliation only means something against an EXTERNAL system: an order
placed by hand in the broker app, a fill we never saw, a position the broker
thinks we hold. None of those can exist when we are the broker.

So this is a LIVE-mode safety net, to be scheduled when live mode is enabled
(`te.risk.live_gate`), not before. Wanting paper-mode coverage of this code
path is reasonable, but the prerequisite is making the simulated broker's
positions persist across cycles -- until then the check can only produce false
halts, never find a real problem.

### Two known divergences from that reference implementation

1. NautilusTrader does NOT halt on mismatch. It treats the venue as the source
   of truth, self-heals its local state, and logs what it cannot resolve --
   the argument being that the broker holds the binding position and the local
   model is only a cache, so a network blip should not stop trading. Halting
   here is a deliberate, more conservative posture for an unattended retail
   engine, and is kept on purpose; it is recorded as a choice rather than an
   oversight.
2. It also separates STARTUP reconciliation (once, before strategies run,
   mandatory) from continuous runtime checks. This module only implements the
   continuous kind. The startup pass is arguably the more valuable of the two,
   since it is what catches a position that appeared while the engine was off.
   Not built yet.
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
