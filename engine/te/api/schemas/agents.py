"""Mirrors the AI-agent-system slice of dashboard/src/types/index.ts.

Per the plan's explicit recommendation, `/api/agents/*`'s
`DiscoveryQueueItem`/`totalStrategiesDiscovered`/`mlLesson` presuppose an
autonomous strategy-discovery loop that is out of scope — these stay
permanently degenerate (`queue: []`, counters `0`, `mlLesson: ""`) rather
than being filled with plausible-looking values.
"""

from typing import Literal

from pydantic import BaseModel


class ResearchBrief(BaseModel):
    date: str | None = None
    timestamp: str | None = None
    sentiment: str | None = None
    summary: str
    confidence: str | float
    details: str
    source: str | None = None


class DailyRecapStrategyLine(BaseModel):
    name: str
    pnl: float
    wins: int
    losses: int


class DailyRecap(BaseModel):
    date: str
    totalPnl: float
    strategies: list[DailyRecapStrategyLine]
    mlLesson: str


class PatternLibraryEntry(BaseModel):
    pattern: str
    condition: str
    winRate: float
    tradesTested: int
    lastObserved: str
    status: Literal["working", "mixed", "no-edge"]


class LearningProgress(BaseModel):
    winRateTrend: list[float]
    totalStrategiesDiscovered: int
    totalStrategiesRetired: int
    avgProfitPerTrade: float
    dates: list[str]
