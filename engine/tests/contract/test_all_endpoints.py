"""The permanent regression net for the frozen 53-endpoint dashboard
contract (`dashboard/src/lib/api.ts`). Parametrised over every (method, path,
body) triple in that file — every endpoint must return 2xx and a body that
validates against the corresponding Pydantic schema mirroring the TS type.

`dashboard/src/lib/api.ts`'s `get()`/`post()` throw on any non-2xx, so a
`501` would render as a red error rather than an `EmptyState` — Phase 0
therefore returns `200`/`201` everywhere with the type's honest zero-state
body (see `te/api/provenance.py` and the plan's "Satisfying the 53 endpoints
honestly" section).
"""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from te.api.main import app
from te.api.schemas.agents import DailyRecap, LearningProgress, PatternLibraryEntry, ResearchBrief
from te.api.schemas.approval import PendingApproval
from te.api.schemas.broker import BrokerStatus
from te.api.schemas.dashboard import (
    CycleEvaluation,
    DailyPnL,
    DashboardData,
    DashboardSnapshot,
    EquityPoint,
    MarketSession,
    ModeResponse,
    WatchlistItem,
)
from te.api.schemas.execution import (
    CycleStatus,
    ExecutionStatus,
    PaperPositionCount,
    PaperTrade,
    SignalFeedItem,
    SkippedSignalInfo,
    StatusOnlyResponse,
)
from te.api.schemas.journal import JournalEntry
from te.api.schemas.learning import (
    EngineStats,
    MaturityGateStatus,
    MLInfo,
    OptimizeResponse,
    RetrainResponse,
    ShadowComparison,
    TrainingResultsEmpty,
)
from te.api.schemas.ops import EngineHealthStatus
from te.api.schemas.pnl import PnLAnalysis
from te.api.schemas.strategy import (
    AgentStrategiesResponse,
    StrategiesFile,
    StrategyAnalysis,
    StrategyCard,
    StrategyConfig,
)
from te.api.schemas.trading import MarketData, Order, Position, RejectedOrder, Trade


class SuccessResponse(BaseModel):
    success: bool


client = TestClient(app)


def _list_of(model: type[BaseModel]) -> Callable[[object], None]:
    def _validate(body: object) -> None:
        assert isinstance(body, list)
        for item in body:
            model.model_validate(item)

    return _validate


def _model(model: type[BaseModel]) -> Callable[[object], None]:
    def _validate(body: object) -> None:
        model.model_validate(body)

    return _validate


def _nullable_model(model: type[BaseModel]) -> Callable[[object], None]:
    def _validate(body: object) -> None:
        if body is not None:
            model.model_validate(body)

    return _validate


# (method, path, json body for POST, expected status, validator)
ENDPOINTS: list[tuple[str, str, dict[str, object] | None, int, Callable[[object], None]]] = [
    ("GET", "/api/mode", None, 200, _model(ModeResponse)),
    ("POST", "/api/mode", {"mode": "dry-run"}, 200, _model(ModeResponse)),
    ("GET", "/api/strategies", None, 200, _list_of(StrategyConfig)),
    ("GET", "/api/dashboard", None, 200, _model(DashboardData)),
    ("GET", "/api/dashboard/snapshot", None, 200, _model(DashboardSnapshot)),
    ("GET", "/api/decisions/today", None, 200, _list_of(CycleEvaluation)),
    ("GET", "/api/approvals", None, 200, _list_of(PendingApproval)),
    (
        "POST",
        "/api/approvals/decide",
        {"id": "nonexistent", "decision": "approve"},
        200,
        _nullable_model(PendingApproval),
    ),
    ("GET", "/api/quotes?symbol=NIFTY&exchange=NSE", None, 200, _list_of(MarketData)),
    ("GET", "/api/history?symbol=NIFTY&exchange=NSE&interval=1m", None, 200, _list_of(MarketData)),
    ("GET", "/api/orders", None, 200, _list_of(Order)),
    (
        "POST",
        "/api/orders/place",
        {
            "symbol": "NIFTY",
            "exchange": "NFO",
            "transactionType": "BUY",
            "quantity": 1,
            "price": 100.0,
            "orderType": "MARKET",
            "productType": "MIS",
        },
        201,
        _model(Order),
    ),
    ("POST", "/api/orders/cancel", {"id": "nonexistent"}, 200, _model(SuccessResponse)),
    ("GET", "/api/positions", None, 200, _list_of(Position)),
    ("POST", "/api/positions/squareoff", {"symbol": "NIFTY", "exchange": "NFO"}, 200, _model(SuccessResponse)),
    ("GET", "/api/trades", None, 200, _list_of(Trade)),
    ("GET", "/api/pnl", None, 200, _model(PnLAnalysis)),
    ("GET", "/api/equity-curve", None, 200, _list_of(EquityPoint)),
    ("GET", "/api/daily-pnl", None, 200, _list_of(DailyPnL)),
    ("GET", "/api/watchlist", None, 200, _list_of(WatchlistItem)),
    ("GET", "/api/market-status", None, 200, _model(MarketSession)),
    ("GET", "/api/journal", None, 200, _list_of(JournalEntry)),
    (
        "POST",
        "/api/journal/save",
        {"date": "2026-07-29", "notes": "test", "emotion": "neutral", "tags": []},
        201,
        _model(JournalEntry),
    ),
    ("GET", "/api/broker-status", None, 200, _model(BrokerStatus)),
    ("GET", "/api/engine/health", None, 200, _model(EngineHealthStatus)),
    ("POST", "/api/engine/pause", {}, 200, _model(EngineHealthStatus)),
    ("POST", "/api/engine/resume", {}, 200, _model(EngineHealthStatus)),
    ("POST", "/api/engine/reset-drawdown-breaker", {}, 200, _model(EngineHealthStatus)),
    ("GET", "/api/rejected-orders", None, 200, _list_of(RejectedOrder)),
    ("GET", "/api/agents/research", None, 200, _model(ResearchBrief)),
    ("GET", "/api/agents/strategies", None, 200, _model(AgentStrategiesResponse)),
    ("POST", "/api/agents/strategies/pause", {"id": "nonexistent", "paused": True}, 200, _nullable_model(StrategyCard)),
    ("GET", "/api/agents/recap", None, 200, _model(DailyRecap)),
    ("GET", "/api/agents/learning", None, 200, _model(LearningProgress)),
    ("GET", "/api/agents/patterns", None, 200, _list_of(PatternLibraryEntry)),
    ("GET", "/api/learning/stats", None, 200, _model(EngineStats)),
    ("GET", "/api/learning/training-results", None, 200, _model(TrainingResultsEmpty)),
    ("GET", "/api/learning/maturity-gate", None, 200, _model(MaturityGateStatus)),
    ("GET", "/api/learning/shadow-comparisons", None, 200, _list_of(ShadowComparison)),
    ("POST", "/api/learning/retrain", {}, 200, _model(RetrainResponse)),
    (
        "POST",
        "/api/learning/optimize",
        {"strategy": "orbs", "param_grid": {"target_rr": [1.0, 1.5]}},
        200,
        _model(OptimizeResponse),
    ),
    ("GET", "/api/execution/status", None, 200, _model(ExecutionStatus)),
    ("GET", "/api/execution/cycle", None, 200, _model(CycleStatus)),
    ("GET", "/api/execution/trades", None, 200, _list_of(PaperTrade)),
    ("GET", "/api/execution/positions", None, 200, _model(PaperPositionCount)),
    ("GET", "/api/execution/skipped", None, 200, _list_of(SkippedSignalInfo)),
    ("POST", "/api/execution/start", {}, 200, _model(StatusOnlyResponse)),
    ("POST", "/api/execution/stop", {}, 200, _model(StatusOnlyResponse)),
    ("GET", "/api/execution/signal-feed", None, 200, _list_of(SignalFeedItem)),
    ("GET", "/api/execution/analysis", None, 200, _model(StrategyAnalysis)),
    ("GET", "/api/execution/ml-info", None, 200, _model(MLInfo)),
    ("GET", "/api/strategies/config", None, 200, _model(StrategiesFile)),
    (
        "POST",
        "/api/strategies/config",
        {
            "check_interval_secs": 0,
            "ml_threshold": 0,
            "max_trades_per_day": 0,
            "risk_per_trade_pct": 0,
            "max_daily_loss_pct": 0,
            "max_drawdown_pct": 0,
            "max_concurrent_positions": 0,
            "instruments": [],
            "strategies": [],
        },
        200,
        _model(StrategiesFile),
    ),
]


def test_all_53_endpoints_are_registered() -> None:
    assert len(ENDPOINTS) == 53


@pytest.mark.parametrize(
    "method,path,body,expected_status,validate",
    ENDPOINTS,
    ids=[f"{m} {p}" for m, p, *_ in ENDPOINTS],
)
def test_endpoint_returns_honest_zero_state(
    method: str,
    path: str,
    body: dict[str, object] | None,
    expected_status: int,
    validate: Callable[[object], None],
) -> None:
    if method == "GET":
        response = client.get(path)
    else:
        response = client.post(path, json=body)

    assert response.status_code == expected_status, response.text
    validate(response.json())

    # Every non-ready endpoint carries the additive provenance headers so the
    # dashboard can render an EmptyState instead of trusting a fabricated
    # number — see te/api/provenance.py.
    assert "X-TE-Provenance" in response.headers
    assert "X-TE-Sample-Size" in response.headers
    assert "X-TE-Not-Ready-Reason" in response.headers

    # A header whose VALUE is never checked is not a check — a route could
    # claim `X-TE-Sample-Size: 1` on every honest zero-state body, or drop
    # its not-ready reason entirely, and this test would still pass without
    # the assertions below. Pin both whenever a route says it has nothing
    # real to show.
    if response.headers["X-TE-Provenance"] == "none":
        assert response.headers["X-TE-Sample-Size"] == "0"
        assert response.headers["X-TE-Not-Ready-Reason"] != ""
