"""Mirrors the dashboard-summary slice of dashboard/src/types/index.ts plus
dashboard/src/types/dashboard-snapshot.ts."""

from typing import Literal

from pydantic import BaseModel

from te.api.schemas.decision import ConditionResult, DecisionVerdict

# ONE definition of the ML maturity ladder — owned by the learning schema,
# aliased here under the name the dashboard slice uses.
from te.api.schemas.learning import MaturityStage as MLStage
from te.api.schemas.trading import MarketData, Position

TradingMode = Literal["dry-run", "live"]
BotStatus = Literal["live", "stale", "paused"]
PipelineStageKey = Literal["fetch", "analyze", "risk", "decide", "act"]
PipelineStageState = Literal["done", "active", "pending"]


class DashboardData(BaseModel):
    dayPnl: float
    dayPnlPercent: float
    winRate: float
    totalTrades: int
    activePositions: int
    positions: list[Position]
    quotes: list[MarketData]


class EquityPoint(BaseModel):
    date: str
    value: float


class DailyPnL(BaseModel):
    date: str
    pnl: float
    trades: int


class MarketSession(BaseModel):
    status: Literal["open", "closed", "pre-open", "post-closed"]
    label: str
    nextEvent: str
    currentTime: str


class WatchlistItem(BaseModel):
    symbol: str
    exchange: str
    ltp: float
    change: float
    changePercent: float


class PipelineStageInfo(BaseModel):
    key: PipelineStageKey
    label: str
    state: PipelineStageState
    durationLabel: str | None = None


class OpenPosition(BaseModel):
    symbol: str
    lots: int
    entryTime: str
    pnl: float


class CycleEvaluation(BaseModel):
    id: str
    timestamp: str
    strategy: str
    instrument: str
    verdict: DecisionVerdict
    reason: str
    conditions: list[ConditionResult]


class ModeResponse(BaseModel):
    mode: TradingMode


class GateFailureResponse(BaseModel):
    """409 response body for `POST /api/mode {mode:'live'}` when
    `te.risk.live_gate.LiveUnlockGate.check()` fails — `failingConditions`
    are real, specific, human-readable reasons (e.g. "DSR 0.62 < required
    0.95"), never a generic placeholder."""

    failingConditions: list[str]


class DashboardSnapshot(BaseModel):
    mode: TradingMode
    status: BotStatus
    asOf: str
    nextCheckInSeconds: int

    todayPnl: float
    dailyLossLimit: float

    openPositionsCount: int
    maxPositions: int
    tradesToday: int
    maxTradesPerDay: int

    positions: list[OpenPosition]
    pipeline: list[PipelineStageInfo]

    weekWinRatePct: float
    weekTrades: int
    weekNetPnl: float
    mlStage: MLStage
