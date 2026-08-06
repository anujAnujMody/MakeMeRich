import datetime as dt

from fastapi import APIRouter, HTTPException, Response

from te.api.db import session_factory
from te.api.provenance import set_provenance
from te.api.schemas.agents import DailyRecap, LearningProgress, PatternLibraryEntry, ResearchBrief
from te.api.schemas.strategy import AgentStrategiesResponse, StrategyCard, StrategyPausePayload
from te.api.strategy_cards import strategy_card
from te.backtest.results_store import load_results
from te.engine.strategy_config import enabled_strategies, set_strategy_enabled
from te.strategy.registry import all_specs, available, spec

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
    """The strategy library, with each strategy's measured result.

    `active`/`inactive` were previously empty on the grounds that no
    autonomous discovery loop exists. That reasoning covered the DISCOVERY
    QUEUE, not the cards: there are now 32 registered strategies with real
    backtests over 2.5 years of option premiums, so returning nothing was
    hiding real measurements rather than avoiding fabricated ones.

    `queue` stays empty and always will — nothing in this project discovers
    strategies on its own, and a populated queue would claim otherwise.

    **`confidence` carries the LUCK-ADJUSTED score.** See
    `te.api.routers.strategies`, which enforces the same rule; the raw
    backtest number must never cross this boundary, because the best of 32
    noise draws looks excellent by construction.
    """
    with session_factory() as session:
        results = load_results(session)
        enabled = enabled_strategies(session)
    cards = [
        strategy_card(spec_obj, enabled=spec_obj.name in enabled, result=results.get(spec_obj.name))
        for spec_obj in all_specs()
    ]
    tested = sum(1 for c in cards if c.totalTrades > 0)
    if tested:
        set_provenance(response, provenance="backtest", sample_size=tested)
    else:
        set_provenance(
            response, not_ready_reason="no backtest has been run yet - see scripts/backtest_all_strategies.py"
        )
    return AgentStrategiesResponse(
        active=[c for c in cards if c.active],
        inactive=[c for c in cards if not c.active],
        queue=[],
    )


@router.post("/strategies/pause", response_model=StrategyCard | None)
def set_agent_strategy_paused(payload: StrategyPausePayload, response: Response) -> StrategyCard | None:
    """Switches one strategy on or off, for real.

    Previously a no-op that returned whatever card happened to match. It now
    writes to `engine_state`, which the paper cycle reads fresh every run —
    so the toggle takes effect on the next cycle without a restart.

    `paused=True` means "not enabled". Enabling `random_entry` is refused by
    `set_strategy_enabled`: the coin-flip control is the yardstick, and one
    that could be armed from the UI is not a control.
    """
    # An unknown id returns `null` with 200 — the documented shape of this
    # endpoint (`StrategyCard | None`) and what the frozen contract test
    # asserts. A 400 here would be a contract break for a case the client
    # already handles.
    if payload.id not in set(available()):
        set_provenance(response, not_ready_reason=f"no strategy named {payload.id!r}")
        return None

    with session_factory() as session:
        try:
            set_strategy_enabled(session, payload.id, enabled=not payload.paused)
            session.commit()
        except ValueError as exc:
            # Reserved for a REAL refusal — currently only an attempt to arm
            # the measure-only control. That deserves to surface as an error
            # rather than a silent no-op, because the caller asked for
            # something the system must never do.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        results = load_results(session)
        enabled = enabled_strategies(session)
    set_provenance(response, provenance="backtest")
    return strategy_card(spec(payload.id), enabled=payload.id in enabled, result=results.get(payload.id))


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
