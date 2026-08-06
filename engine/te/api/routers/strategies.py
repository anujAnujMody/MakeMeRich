from fastapi import APIRouter, Response

from te.api.provenance import set_provenance
from te.api.schemas.strategy import StrategiesFile, StrategyConfig
from te.api.state import state

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


@router.get("", response_model=list[StrategyConfig])
def list_strategies(response: Response) -> list[StrategyConfig]:
    """Registered strategies and their enabled state. Empty until strategies
    are loaded."""
    set_provenance(response, not_ready_reason="phase-0: no strategies loaded yet")
    return []


@router.get("/config", response_model=StrategiesFile)
def get_strategies_config(response: Response) -> StrategiesFile:
    """The full strategy-configuration file. Returns the in-memory default
    until `config/strategies.yaml` is loaded from disk."""
    set_provenance(response, not_ready_reason="phase-0: config/strategies.yaml not loaded yet")
    return state.strategies_config


@router.post("/config", response_model=StrategiesFile)
def put_strategies_config(payload: StrategiesFile, response: Response) -> StrategiesFile:
    """Replaces the strategy-configuration file. Held in memory only — the
    change does not survive a restart until config persistence exists."""
    set_provenance(response, not_ready_reason="phase-0: config is held in memory only, not persisted yet")
    state.strategies_config = payload
    return state.strategies_config
