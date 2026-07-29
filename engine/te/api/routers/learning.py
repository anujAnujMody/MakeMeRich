from fastapi import APIRouter, Response

from te.api.provenance import set_provenance
from te.api.schemas.learning import (
    EngineStats,
    MaturityGateStatus,
    OptimizeRequest,
    OptimizeResponse,
    RetrainResponse,
    ShadowComparison,
    TrainingResultsEmpty,
)

router = APIRouter(prefix="/api/learning", tags=["learning"])


@router.get("/stats", response_model=EngineStats)
def get_learning_stats(response: Response, strategy: str | None = None, symbol: str | None = None) -> EngineStats:
    """Aggregate performance stats, optionally scoped to one strategy or
    symbol. All zero until trades are recorded — never a fabricated win rate."""
    set_provenance(response, not_ready_reason="phase-0: no trades recorded yet")
    return EngineStats(total_trades=0, win_rate=0, avg_profit=0, sharpe=0, profit_factor=0, max_drawdown=0)


@router.get("/training-results", response_model=TrainingResultsEmpty)
def get_training_results(response: Response) -> TrainingResultsEmpty:
    """Most recent model-training run's results. Empty until a model has
    been trained."""
    set_provenance(response, not_ready_reason="phase-0: no model trained yet")
    return TrainingResultsEmpty()


@router.get("/maturity-gate", response_model=MaturityGateStatus)
def get_maturity_gate(response: Response) -> MaturityGateStatus:
    """ML maturity stage and each promotion criterion's current progress.
    Counters are zero until paper trades exist; `dsr` stays `None` rather
    than reporting a number no sample supports."""
    set_provenance(response, not_ready_reason="phase-0: no paper trades exist yet")
    return MaturityGateStatus(
        currentStage="shadow",
        closedPaperTrades=0,
        dsr=None,
        filteredEdgePositiveSessions=0,
        consecutiveGatingSessionsOnPaper=0,
    )


@router.get("/shadow-comparisons", response_model=list[ShadowComparison])
def get_shadow_comparisons(response: Response) -> list[ShadowComparison]:
    """Shadow-model calls compared against what actually happened. Empty
    until a shadow-mode model exists."""
    set_provenance(response, not_ready_reason="phase-0: no shadow-mode model exists yet")
    return []


@router.post("/retrain", response_model=RetrainResponse)
def retrain_model(response: Response) -> RetrainResponse:
    """Triggers a model retrain. Reports `not_ready` until a training
    pipeline exists — it never claims a run that did not happen."""
    set_provenance(response, not_ready_reason="phase-0: no training pipeline exists yet")
    return RetrainResponse(status="not_ready", trades_used=0, accuracy=0, top_features=[])


@router.post("/optimize", response_model=OptimizeResponse)
def optimize_params(payload: OptimizeRequest, response: Response) -> OptimizeResponse:
    """Parameter grid search. Deliberately always returns no results.

    Per the plan's explicit recommendation: `ParamSuggestion` has no
    provenance field, so any real grid search would report unlabelled
    in-sample numbers on every click. Always `results: []`."""
    set_provenance(response, not_ready_reason="phase-0: optimize is intentionally a no-op, see plan R-notes")
    return OptimizeResponse(results=[])
