import datetime as dt

from fastapi import APIRouter, Request, Response

from te.api.db import session_factory
from te.api.provenance import set_provenance
from te.api.schemas.broker import BrokerStatus
from te.domain.clock import IST
from te.persistence.repos.paper_trading import order_ids_today

# Prefix is only `/api`: this router's single path (`/api/broker-status`) has
# no deeper shared segment, and the resolved URL is a frozen contract.
router = APIRouter(prefix="/api", tags=["broker"])


@router.get("/broker-status", response_model=BrokerStatus)
def get_broker_status(request: Request, response: Response) -> BrokerStatus:
    """Broker connectivity and today's API usage — from real state.

    `connected` reads `WSRecorderSupervisor.is_running()` off
    `request.app.state.ws_supervisor` (set by `te.api.main`'s lifespan) —
    was a literal Phase-0 stub hardcoding `connected=False` forever, found
    live on 2026-07-30: the Ops page showed "Disconnected"/"Down" all day
    despite the broker actually streaming ticks continuously. `latency`
    and `lastPing` have no real measurement wired up yet — stay honestly
    `0`/`""` rather than fabricated, same discipline as `sharpe`/
    `dayPnlPercent` elsewhere."""
    supervisor = getattr(request.app.state, "ws_supervisor", None)
    connected = supervisor.is_running() if supervisor is not None else False

    with session_factory() as session:
        orders_today = len(order_ids_today(session, dt.datetime.now(IST).date()))

    # Provenance reflects the RESPONSE BODY, not just the live WS link — a
    # disconnect mid-session doesn't erase orders already placed earlier
    # that day. Found by review: this used to key off `connected` alone,
    # so a real non-zero `ordersToday` could be tagged `provenance=none`
    # (i.e. "nothing real here") the moment the broker dropped.
    set_provenance(response, provenance="paper" if (connected or orders_today) else "none", sample_size=orders_today)
    return BrokerStatus(
        connected=connected,
        name="OpenAlgo",
        latency=0,
        lastPing="",
        lastSync="",
        ordersToday=orders_today,
        apiCalls=0,
    )
