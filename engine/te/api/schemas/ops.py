"""Mirrors dashboard/src/types/ops.ts."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel

DailyLossState = Literal["normal", "reduced", "halted_day"]
EngineRunState = Literal["running", "paused"]


class EngineHealthStatus(BaseModel):
    runState: EngineRunState
    dailyLossState: DailyLossState
    drawdownBreakerTripped: bool
    currentDrawdownPct: float
    maxDrawdownLimitPct: float
    lastSuccessfulPollSecondsAgo: float


class SchedulerJobStatus(BaseModel):
    """One APScheduler job's registration info — used to prove the paper
    cycle job (and the three Phase 1 jobs) are actually registered, not
    just present in code."""

    id: str
    nextRunTime: dt.datetime | None
    maxInstances: int


class SchedulerStatus(BaseModel):
    """`GET /api/engine/scheduler-status` — confirms the scheduler's jobs
    are registered, and the paper-trading cycle job's last-run time/result
    (`None`/`None` if it has never run yet, e.g. right after startup, before
    market hours)."""

    jobs: list[SchedulerJobStatus]
    paperCycleLastRunAt: dt.datetime | None
    paperCycleLastResult: str | None
    #: From `WSRecorderSupervisor.feed_health` — proves ticks are actually
    #: arriving, not just that the WS thread is alive. `None`/`None` before
    #: the first `ws_feed_health_check` run (e.g. before the recorder has
    #: started for the day).
    feedLastCheckedAt: dt.datetime | None = None
    feedLastTickAt: dt.datetime | None = None
