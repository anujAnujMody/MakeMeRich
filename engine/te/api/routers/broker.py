from fastapi import APIRouter, Response

from te.api.provenance import set_provenance
from te.api.schemas.broker import BrokerStatus

# Prefix is only `/api`: this router's single path (`/api/broker-status`) has
# no deeper shared segment, and the resolved URL is a frozen contract.
router = APIRouter(prefix="/api", tags=["broker"])


@router.get("/broker-status", response_model=BrokerStatus)
def get_broker_status(response: Response) -> BrokerStatus:
    """Broker connectivity and today's API usage. Reports disconnected until
    a broker connection is wired up."""
    set_provenance(response, not_ready_reason="phase-0: no broker connection wired yet")
    return BrokerStatus(
        connected=False,
        name="",
        latency=0,
        lastPing="",
        lastSync="",
        ordersToday=0,
        apiCalls=0,
    )
