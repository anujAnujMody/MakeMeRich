from fastapi import APIRouter, Request, Response

from te.api.provenance import set_provenance
from te.api.schemas.ops import EngineHealthStatus, SchedulerJobStatus, SchedulerStatus
from te.api.state import state

router = APIRouter(prefix="/api/engine", tags=["engine"])


@router.get("/scheduler-status", response_model=SchedulerStatus)
def get_scheduler_status(request: Request) -> SchedulerStatus:
    """Confirms the scheduler's jobs (including the Phase 4/6/7 paper-cycle
    job — see `te.engine.scheduler.PaperCycleRunner`) are actually
    registered, and reports the paper cycle job's last-run time/result.
    Reads `request.app.state.scheduler`/`.paper_cycle_runner`, set by
    `te.api.main`'s lifespan — `None` for both (an empty job list) only if
    read before the lifespan has started, which does not happen in normal
    operation."""
    scheduler = getattr(request.app.state, "scheduler", None)
    runner = getattr(request.app.state, "paper_cycle_runner", None)

    jobs = (
        [
            SchedulerJobStatus(id=job.id, nextRunTime=job.next_run_time, maxInstances=job.max_instances)
            for job in scheduler.get_jobs()
        ]
        if scheduler is not None
        else []
    )
    return SchedulerStatus(
        jobs=jobs,
        paperCycleLastRunAt=runner.status.last_run_at if runner is not None else None,
        paperCycleLastResult=runner.status.last_result if runner is not None else None,
    )


@router.get("/health", response_model=EngineHealthStatus)
def get_engine_health(response: Response) -> EngineHealthStatus:
    """Engine run state, drawdown-breaker status and last-cycle health.
    Zero-state until engine cycles run."""
    set_provenance(response, not_ready_reason="phase-0: no engine cycle runs yet")
    return state.engine_health


@router.post("/pause", response_model=EngineHealthStatus)
def pause_engine(response: Response) -> EngineHealthStatus:
    """Pauses the engine, returning the updated health status. Flips
    in-memory state only until engine cycles are wired up."""
    set_provenance(response, not_ready_reason="phase-0: no engine cycle runs yet")
    state.engine_health = state.engine_health.model_copy(update={"runState": "paused"})
    return state.engine_health


@router.post("/resume", response_model=EngineHealthStatus)
def resume_engine(response: Response) -> EngineHealthStatus:
    """Resumes the engine, returning the updated health status. Flips
    in-memory state only until engine cycles are wired up."""
    set_provenance(response, not_ready_reason="phase-0: no engine cycle runs yet")
    state.engine_health = state.engine_health.model_copy(update={"runState": "running"})
    return state.engine_health


@router.post("/reset-drawdown-breaker", response_model=EngineHealthStatus)
def reset_drawdown_breaker(response: Response) -> EngineHealthStatus:
    """Manually clears a tripped drawdown breaker. Flips in-memory state only
    until engine cycles are wired up."""
    set_provenance(response, not_ready_reason="phase-0: no engine cycle runs yet")
    state.engine_health = state.engine_health.model_copy(
        update={"drawdownBreakerTripped": False, "currentDrawdownPct": 0.0}
    )
    return state.engine_health
