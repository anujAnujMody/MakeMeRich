"""Mirrors dashboard/src/types/index.ts's learning/ML slice and
dashboard/src/types/learning.ts."""

from typing import Literal

from pydantic import BaseModel, Field

MaturityStage = Literal["shadow", "advisory", "gating", "live-gating"]


class EngineStats(BaseModel):
    total_trades: int
    win_rate: float
    avg_profit: float
    sharpe: float
    profit_factor: float
    max_drawdown: float


class ParamSuggestion(BaseModel):
    params: dict[str, float]
    score: float
    win_rate: float
    total_pnl: float
    sharpe: float
    total_trades: int


class OptimizeRequest(BaseModel):
    strategy: str = Field(min_length=1)
    param_grid: dict[str, list[float]]


class OptimizeResponse(BaseModel):
    """No provenance field exists on `ParamSuggestion`, so any grid search
    would report unlabelled in-sample numbers on every click. Per the plan's
    explicit recommendation: always `results: []`."""

    results: list[ParamSuggestion] = []


class WalkForwardSummary(BaseModel):
    oos_sharpe: float
    profit_factor: float
    total_trades: int


class TrainingResultsEmpty(BaseModel):
    status: Literal["no_training_results"] = "no_training_results"


# TrainingResults is a discriminated-ish union in TS; FastAPI/Pydantic route
# handlers return TrainingResultsEmpty directly at Phase 0 (nothing trained
# yet), so no explicit Union response_model is needed here.


class MaturityGateStatus(BaseModel):
    currentStage: MaturityStage
    closedPaperTrades: int
    # Deflated Sharpe Ratio on purged CV — null until enough trades exist.
    dsr: float | None = None
    filteredEdgePositiveSessions: int
    consecutiveGatingSessionsOnPaper: int


class ShadowComparison(BaseModel):
    id: str
    timestamp: str
    instrument: str
    actualVerdict: Literal["traded", "skipped"]
    modelVerdict: Literal["would trade", "would skip"]
    agreed: bool


class MLInfoIndexStatus(BaseModel):
    symbol: str
    status: str
    bars: int
    features: int
    pos_pct: float


class MLInfo(BaseModel):
    """No MSW reference body existed for GET /api/execution/ml-info; specced
    from the `MLInfo` TS type alone (see plan's flagged endpoints)."""

    status: str
    accuracy: float
    threshold: float
    samples: int
    trained_on: str
    walk_forward_sharpe: float
    profit_factor: float
    indices: list[MLInfoIndexStatus]


class RetrainResponse(BaseModel):
    status: str
    trades_used: int
    accuracy: float
    top_features: list[float]
