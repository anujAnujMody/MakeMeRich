"""Mirrors the execution/paper-trading and pipeline/cycle-status slices of
dashboard/src/types/index.ts."""

from typing import Literal

from pydantic import BaseModel

PipelineStage = Literal["idle", "fetching", "analyzing", "scoring", "deciding", "exiting"]


class ExecutionStatus(BaseModel):
    running: bool
    check_interval_secs: int
    ml_threshold: float
    started_at: str | None = None


class PaperTrade(BaseModel):
    id: str
    strategy: str
    symbol: str
    direction: str
    entry_price: float
    exit_price: float | None = None
    quantity: float
    pnl: float | None = None
    outcome: str | None = None
    entry_time: str
    exit_time: str | None = None
    ml_confidence: float | None = None
    type: str | None = None


class PaperPositionCount(BaseModel):
    count: int


class SkippedSignalInfo(BaseModel):
    id: int
    strategy: str
    symbol: str
    direction: str
    entry_price: float
    ml_confidence: float
    ml_threshold: float
    reason: str
    timestamp: str


class CycleHistoryEntry(BaseModel):
    timestamp: str
    duration_secs: float
    instruments_processed: int
    instruments_total: int
    signals_generated: int
    trades_placed: int
    trades_skipped: int
    error: str | None = None


class CycleStatus(BaseModel):
    stage: PipelineStage
    running: bool
    cycle_start: str | None = None
    next_cycle_in_secs: int
    instruments_processed: int
    instruments_total: int
    signals_generated: int
    trades_placed: int
    trades_skipped: int
    last_error: str | None = None
    history: list[CycleHistoryEntry] | None = None


class SignalFeedItem(BaseModel):
    id: str
    type: Literal["placed", "skipped"]
    strategy: str
    symbol: str
    direction: str
    entry_price: float
    quantity: float | None = None
    ml_confidence: float
    reason: str | None = None
    timestamp: str


class StatusOnlyResponse(BaseModel):
    status: str
