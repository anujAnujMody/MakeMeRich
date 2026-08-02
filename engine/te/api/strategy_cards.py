"""Builds the `StrategyCard` the dashboard renders.

Lives in one place so the rule below holds wherever a card is built. Today
only `/api/agents/strategies` serves it — `/api/strategies` is still the
phase-0 stub returning `[]` — but a second copy would eventually disagree
with this one, and the disagreement would be a number on a page.

### The rule

**`confidence` carries the LUCK-ADJUSTED score, never the raw backtest
number.**

Listing 32 strategies with their raw results is a machine for manufacturing
false winners — the best of N noise draws looks excellent by construction,
and this project has already demonstrated that on itself twice (85 rule/exit
combinations whose best was luck, and four open-interest features that
reversed sign on older data). `te.backtest.strategy_lab` deflates every score
against the honest count of every trial ever run, and only that number is
allowed out.

An untested strategy reports zeros, not a placeholder that reads like a
measurement — the plan's standing ban on showing a win rate or confidence
without a real sample behind it.
"""

from __future__ import annotations

from te.api.schemas.strategy import StrategyCard
from te.backtest.results_store import StoredResult
from te.strategy.spec import StrategySpec


def strategy_card(spec: StrategySpec, *, enabled: bool, result: StoredResult | None) -> StrategyCard:
    # A result with no trades behind it is not a measurement, so it is
    # discarded here rather than re-checked at every field below.
    tested = result if (result is not None and result.trades > 0) else None
    return StrategyCard(
        id=spec.name,
        name=spec.name.replace("_", " ").title(),
        description=spec.summary,
        active=enabled,
        winRate=round(tested.win_rate * 100, 1) if tested else 0.0,
        # The deflated score, as a percentage. Not the win rate, not the raw
        # Sharpe — see the module docstring.
        confidence=round(tested.deflated * 100, 1) if tested else 0.0,
        # "up" is reserved for a strategy that genuinely cleared the bar.
        # Nothing in the library has, and a trend arrow implying otherwise
        # would be the smallest possible lie with the largest consequence.
        confidenceTrend="up" if (tested and tested.beats_luck) else "stable",
        # No live P&L exists for any of these — none has traded real money.
        # A backtest figure here would be read as realised profit.
        weeklyPnl=0.0,
        totalTrades=tested.trades if tested else 0,
        paused=not enabled,
        pauseReason=None if enabled else ("measure-only control" if not spec.tradeable else "switched off"),
        lastActive=tested.measured_at if tested else "",
        rule=spec.summary,
    )
