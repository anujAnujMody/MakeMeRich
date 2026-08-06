from fastapi import APIRouter, HTTPException, Response

from te.api.db import session_factory
from te.api.provenance import set_provenance
from te.api.schemas.dashboard import GateFailureResponse, ModeResponse, TradingMode
from te.engine.state import get_mode, set_mode
from te.persistence.db import session_scope
from te.risk.live_gate import LiveUnlockGate

router = APIRouter(prefix="/api/mode", tags=["mode"])


@router.get("", response_model=ModeResponse)
def get_mode_endpoint(response: Response) -> ModeResponse:
    """Current trading mode, read from the persisted `engine_state` row —
    never from config or an environment variable."""
    with session_factory() as session:
        mode = get_mode(session)
    set_provenance(response, provenance="paper", sample_size=1, not_ready_reason="")
    return ModeResponse(mode=mode)


@router.post("", response_model=ModeResponse)
def set_mode_endpoint(payload: ModeResponse, response: Response) -> ModeResponse:
    """Switches trading mode. Switching INTO `live` is gated by
    `LiveUnlockGate` and returns 409 with the failing conditions if any
    criterion is unmet; dry-run/paper switches are never gated."""
    mode: TradingMode = payload.mode
    if mode == "live":
        # Only the switch INTO live money is gated — dry-run/paper switches
        # are never gated, per the plan. `LiveUnlockGate.check()` reads real
        # persisted data (DSR/PBO, post-freeze paper sessions, Tier-0
        # slippage, ML maturity stage) and never fabricates a pass.
        gate = LiveUnlockGate(session_factory)
        result = gate.check()
        if not result.passed:
            raise HTTPException(
                status_code=409,
                detail=GateFailureResponse(failingConditions=list(result.failing_conditions)).model_dump(),
            )
    with session_scope(session_factory) as session:
        set_mode(session, mode)
        persisted = get_mode(session)
    set_provenance(response, provenance="paper", sample_size=1, not_ready_reason="")
    return ModeResponse(mode=persisted)
