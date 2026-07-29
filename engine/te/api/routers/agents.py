import datetime as dt

from fastapi import APIRouter, Response

from te.api.provenance import set_provenance
from te.api.schemas.agents import DailyRecap, LearningProgress, PatternLibraryEntry, ResearchBrief
from te.api.schemas.strategy import AgentStrategiesResponse, StrategyCard, StrategyPausePayload
from te.api.state import state

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/research", response_model=ResearchBrief)
def get_research_brief(response: Response) -> ResearchBrief:
    """Latest research-agent brief. Returns the honest empty brief until a
    research agent actually runs."""
    set_provenance(response, not_ready_reason="phase-0: no research agent runs yet")
    return ResearchBrief(
        date=None,
        timestamp=None,
        sentiment=None,
        summary="",
        confidence=0,
        details="",
        source=None,
    )


@router.get("/strategies", response_model=AgentStrategiesResponse)
def get_agent_strategies(response: Response) -> AgentStrategiesResponse:
    """Agent-discovered strategy cards. There is no autonomous
    strategy-discovery loop in scope, so this stays at its zero-state."""
    set_provenance(response, not_ready_reason="phase-0: no autonomous strategy-discovery loop in scope")
    return state.agent_strategies


@router.post("/strategies/pause", response_model=StrategyCard | None)
def set_agent_strategy_paused(payload: StrategyPausePayload, response: Response) -> StrategyCard | None:
    """Pauses one agent-discovered strategy card, returning it (or `None` if
    no card matches). No discovery loop is in scope, so nothing is acted on."""
    set_provenance(response, not_ready_reason="phase-0: no autonomous strategy-discovery loop in scope")
    return state.find_agent_strategy_card(payload.id)


@router.get("/recap", response_model=DailyRecap)
def get_daily_recap(response: Response, date: str | None = None) -> DailyRecap:
    """End-of-day recap for `date` (today if omitted). Zero-state until a
    trading day has actually been recorded."""
    set_provenance(response, not_ready_reason="phase-0: no trading day recorded yet")
    return DailyRecap(
        date=date or dt.datetime.now(dt.UTC).date().isoformat(),
        totalPnl=0,
        strategies=[],
        mlLesson="",
    )


@router.get("/learning", response_model=LearningProgress)
def get_agents_learning(response: Response) -> LearningProgress:
    """Learning-loop progress over time. Empty series until the learning loop
    runs."""
    set_provenance(response, not_ready_reason="phase-0: no learning loop runs yet")
    return LearningProgress(
        winRateTrend=[],
        totalStrategiesDiscovered=0,
        totalStrategiesRetired=0,
        avgProfitPerTrade=0,
        dates=[],
    )


@router.get("/patterns", response_model=list[PatternLibraryEntry])
def get_pattern_library(response: Response) -> list[PatternLibraryEntry]:
    """Recognised market-pattern library. Empty until a pattern library is
    populated."""
    set_provenance(response, not_ready_reason="phase-0: no pattern library populated yet")
    return []
