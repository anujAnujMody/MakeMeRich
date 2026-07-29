from fastapi import APIRouter, Query, Response

from te.api.provenance import set_provenance
from te.api.schemas.execution import (
    CycleStatus,
    ExecutionStatus,
    PaperPositionCount,
    PaperTrade,
    SignalFeedItem,
    SkippedSignalInfo,
    StatusOnlyResponse,
)
from te.api.schemas.learning import MLInfo
from te.api.schemas.strategy import StrategyAnalysis
from te.api.state import state

router = APIRouter(prefix="/api/execution", tags=["execution"])

#: Shared bounds for every `limit` query parameter in this router. Unbounded
#: `limit: int = 100` accepted negatives and arbitrarily large values.
_LIMIT = Query(default=100, ge=1, le=500)


@router.get("/status", response_model=ExecutionStatus)
def get_execution_status(response: Response) -> ExecutionStatus:
    """Whether the paper-trading loop is running. Reports the honest
    not-running zero-state until that loop is wired up."""
    set_provenance(response, not_ready_reason="phase-0: paper-trading loop not wired yet")
    return ExecutionStatus(running=state.execution_running, check_interval_secs=0, ml_threshold=0, started_at=None)


@router.get("/cycle", response_model=CycleStatus)
def get_execution_cycle(response: Response) -> CycleStatus:
    """Progress of the current engine cycle. Idle zero-state until the
    paper-trading loop is wired up."""
    set_provenance(response, not_ready_reason="phase-0: paper-trading loop not wired yet")
    return CycleStatus(
        stage="idle",
        running=state.execution_running,
        cycle_start=None,
        next_cycle_in_secs=0,
        instruments_processed=0,
        instruments_total=0,
        signals_generated=0,
        trades_placed=0,
        trades_skipped=0,
        last_error=None,
        history=None,
    )


@router.get("/trades", response_model=list[PaperTrade])
def get_execution_trades(response: Response, limit: int = _LIMIT) -> list[PaperTrade]:
    """Most recent paper trades, newest first. Empty until the paper-trading
    loop records its first trade."""
    set_provenance(response, not_ready_reason="phase-0: no paper trades recorded yet")
    return []


@router.get("/positions", response_model=PaperPositionCount)
def get_execution_positions(response: Response) -> PaperPositionCount:
    """Count of currently open paper positions. Zero until the paper-trading
    loop opens one."""
    set_provenance(response, not_ready_reason="phase-0: no paper positions exist yet")
    return PaperPositionCount(count=0)


@router.get("/skipped", response_model=list[SkippedSignalInfo])
def get_execution_skipped(response: Response, limit: int = _LIMIT) -> list[SkippedSignalInfo]:
    """Signals that fired but were not traded, with the reason each was
    skipped. Empty until signals are evaluated."""
    set_provenance(response, not_ready_reason="phase-0: no signals evaluated yet")
    return []


@router.post("/start", response_model=StatusOnlyResponse)
def start_execution(response: Response) -> StatusOnlyResponse:
    """Starts the paper-trading loop. Only flips in-memory state — the loop
    itself is not implemented yet."""
    set_provenance(response, not_ready_reason="phase-0: paper-trading loop not implemented yet")
    state.execution_running = True
    return StatusOnlyResponse(status="started")


@router.post("/stop", response_model=StatusOnlyResponse)
def stop_execution(response: Response) -> StatusOnlyResponse:
    """Stops the paper-trading loop. Only flips in-memory state — the loop
    itself is not implemented yet."""
    set_provenance(response, not_ready_reason="phase-0: paper-trading loop not implemented yet")
    state.execution_running = False
    return StatusOnlyResponse(status="stopped")


@router.get("/signal-feed", response_model=list[SignalFeedItem])
def get_signal_feed(response: Response, limit: int = Query(default=50, ge=1, le=500)) -> list[SignalFeedItem]:
    """Live feed of evaluated signals. Empty until signals are evaluated."""
    set_provenance(response, not_ready_reason="phase-0: no signals evaluated yet")
    return []


@router.get("/analysis", response_model=StrategyAnalysis)
def get_execution_analysis(response: Response) -> StrategyAnalysis:
    """Per-strategy evaluation snapshot for the current cycle. Empty until
    strategy evaluation runs.

    No MSW reference body existed for this endpoint; specced from the
    `StrategyAnalysis` TS type alone (see plan's flagged endpoints)."""
    set_provenance(response, not_ready_reason="phase-0: no strategy evaluation runs yet")
    return StrategyAnalysis(snapshot={}, vix=0, quotes=[], combos=[], timestamp="")


@router.get("/ml-info", response_model=MLInfo)
def get_ml_info(response: Response) -> MLInfo:
    """Current secondary-model status and headline metrics. Reports
    `not_trained` until a model exists.

    No MSW reference body existed for this endpoint; specced from the
    `MLInfo` TS type alone (see plan's flagged endpoints)."""
    set_provenance(response, not_ready_reason="phase-0: no model trained yet")
    return MLInfo(
        status="not_trained",
        accuracy=0,
        threshold=0,
        samples=0,
        trained_on="",
        walk_forward_sharpe=0,
        profit_factor=0,
        indices=[],
    )
