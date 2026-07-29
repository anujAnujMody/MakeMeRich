"""Mirrors the strategy-related slices of dashboard/src/types/index.ts."""

from typing import Any, Literal

from pydantic import BaseModel


class StrategyInstrument(BaseModel):
    symbol: str
    exchange: str
    rangeMin: float
    maxTrades: int


class StrategyConfig(BaseModel):
    name: str
    enabled: bool
    instruments: list[StrategyInstrument]
    params: dict[str, Any]


class StrategyCard(BaseModel):
    id: str
    name: str
    description: str
    active: bool
    winRate: float
    weeklyPnl: float
    confidence: float
    confidenceTrend: Literal["up", "down", "stable"]
    totalTrades: int
    paused: bool
    pauseReason: str | None = None
    lastActive: str
    rule: str


class DiscoveryQueueItem(BaseModel):
    name: str
    progress: float
    status: Literal["testing", "validating", "ready"]


class AgentStrategiesResponse(BaseModel):
    active: list[StrategyCard]
    inactive: list[StrategyCard]
    queue: list[DiscoveryQueueItem]


class StrategyPausePayload(BaseModel):
    id: str
    paused: bool


class StrategyCondition(BaseModel):
    label: str
    met: bool


class StrategyComboAnalysis(BaseModel):
    strategy: str
    symbol: str
    current_price: float | None = None
    error: str | None = None
    reason: str | None = None
    signal: str | None = None
    # ORBS fields
    range_high: float | None = None
    range_low: float | None = None
    range_mid: float | None = None
    buffer: float | None = None
    breakout_level: float | None = None
    breakdown_level: float | None = None
    is_breakout: bool | None = None
    is_breakdown: bool | None = None
    range_pct: float | None = None
    # VWAP fields
    vwap: float | None = None
    deviation_pct: float | None = None
    rsi: float | None = None
    deviation_threshold: float | None = None
    rsi_oversold: float | None = None
    rsi_overbought: float | None = None
    is_long: bool | None = None
    is_short: bool | None = None
    # Shared
    conditions: list[StrategyCondition] | None = None


class QuoteSnapshot(BaseModel):
    symbol: str
    ltp: float
    change: float
    changePercent: float


class StrategyAnalysis(BaseModel):
    """No MSW reference body existed for GET /api/execution/analysis; specced
    from the `StrategyAnalysis` TS type alone (see plan's flagged endpoints)."""

    snapshot: dict[str, float]
    vix: float
    quotes: list[QuoteSnapshot]
    combos: list[StrategyComboAnalysis]
    timestamp: str


class StrategyInstrumentConfig(BaseModel):
    symbol: str
    exchange: str
    ticker: str
    active: bool
    lot_size: int


class StrategyEntry(BaseModel):
    name: str
    active: bool
    instruments: list[str]
    params: dict[str, Any]


class StrategiesFile(BaseModel):
    check_interval_secs: int
    ml_threshold: float
    max_trades_per_day: int
    risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_drawdown_pct: float
    max_concurrent_positions: int
    instruments: list[StrategyInstrumentConfig]
    strategies: list[StrategyEntry]
