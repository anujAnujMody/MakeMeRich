"""Phase-0 in-memory mutable state for the handful of endpoints that accept
writes (mode switch, engine pause/resume, order placement, approval
decisions, ...).

This is intentionally NOT persistence — Phase 3 (execution core) and Phase 4
(paper trading) replace this with the real event-sourced store. Phase 0's
job is only to prove the 53-endpoint contract is honestly satisfiable; every
value here starts at its honest zero-state and this module holds no
fabricated numbers.
"""

from dataclasses import dataclass, field

from te.api.schemas.approval import PendingApproval
from te.api.schemas.dashboard import TradingMode
from te.api.schemas.ops import EngineHealthStatus
from te.api.schemas.strategy import AgentStrategiesResponse, StrategiesFile, StrategyCard
from te.api.schemas.trading import Order


def _initial_engine_health() -> EngineHealthStatus:
    return EngineHealthStatus(
        runState="paused",
        dailyLossState="normal",
        drawdownBreakerTripped=False,
        currentDrawdownPct=0.0,
        maxDrawdownLimitPct=0.0,
        lastSuccessfulPollSecondsAgo=0.0,
    )


def _initial_strategies_config() -> StrategiesFile:
    return StrategiesFile(
        check_interval_secs=0,
        ml_threshold=0.0,
        max_trades_per_day=0,
        risk_per_trade_pct=0.0,
        max_daily_loss_pct=0.0,
        max_drawdown_pct=0.0,
        max_concurrent_positions=0,
        instruments=[],
        strategies=[],
    )


def _initial_agent_strategies() -> AgentStrategiesResponse:
    return AgentStrategiesResponse(active=[], inactive=[], queue=[])


@dataclass
class ApiState:
    """A pure field bag — every field starts at its honest zero-state."""

    mode: TradingMode = "dry-run"
    orders: list[Order] = field(default_factory=list)
    approvals: list[PendingApproval] = field(default_factory=list)
    engine_health: EngineHealthStatus = field(default_factory=_initial_engine_health)
    strategies_config: StrategiesFile = field(default_factory=_initial_strategies_config)
    agent_strategies: AgentStrategiesResponse = field(default_factory=_initial_agent_strategies)
    execution_running: bool = False

    def reset(self) -> None:
        """Restores every field to its initial value IN PLACE, so the
        module-level `state` singleton every router imported by reference
        stays the same object. Rebinding the fields from a throwaway
        instance keeps this correct automatically as fields are added —
        unlike re-calling `__init__()`, which is not a supported way to
        re-run a dataclass's generated initializer and needed a
        `# type: ignore` to silence."""
        self.__dict__.update(vars(ApiState()))

    def find_agent_strategy_card(self, card_id: str) -> StrategyCard | None:
        for card in [*self.agent_strategies.active, *self.agent_strategies.inactive]:
            if card.id == card_id:
                return card
        return None


state = ApiState()
