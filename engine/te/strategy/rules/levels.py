"""Level rules — yesterday's price marks, used as today's support and
resistance.

Both are computed from the PREVIOUS session only. That is what makes them
legitimately usable from the first minute of the day: the inputs were fixed
at yesterday's close, so there is nothing about today for them to peek at.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from te.domain.evaluation import ConditionResult
from te.strategy.indicators import camarilla_levels, pivot_levels
from te.strategy.rules.breakout import _crossed_out
from te.strategy.session_rule import RuleDecision, SessionRule
from te.strategy.spec import StrategySpec


def _previous_session(session: pd.DataFrame) -> tuple[float, float, float] | None:
    high = float(session["previous_high"].iloc[0])
    low = float(session["previous_low"].iloc[0])
    close = float(session["previous_close"].iloc[0])
    if pd.isna(high) or pd.isna(low) or pd.isna(close):
        return None
    return high, low, close


class CamarillaBreak(SessionRule):
    spec = StrategySpec(
        name="camarilla",
        family="levels",
        summary="Uses popular Indian support and resistance levels from yesterday, and trades a break of them.",
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        del params
        previous = _previous_session(session)
        if previous is None:
            return RuleDecision.skip("no previous session recorded to take levels from")
        levels = camarilla_levels(*previous)
        close = float(session["c"].iloc[-1])
        # H4/L4 are the BREAKOUT band in the standard reading — H3/L3 are
        # the reversal band and are deliberately not traded here, so this
        # rule tests one idea rather than two blended together.
        broke = ConditionResult(
            label="price broke the outer level",
            required=f"close above {levels['h4']:.2f} or below {levels['l4']:.2f}",
            actual=f"close={close:.2f}",
            passed=close > levels["h4"] or close < levels["l4"],
            evaluated=True,
        )
        direction = _crossed_out(session, levels["h4"], levels["l4"])
        if direction is None:
            return RuleDecision.skip("inside the levels, or the break already happened", conditions=[broke])
        return RuleDecision.enter(direction, conditions=[broke], reason="broke the outer level")


class PivotBreak(SessionRule):
    spec = StrategySpec(
        name="classic_pivots",
        family="levels",
        summary="The standard floor-trader levels from yesterday, traded on a break.",
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        del params
        previous = _previous_session(session)
        if previous is None:
            return RuleDecision.skip("no previous session recorded to take levels from")
        levels = pivot_levels(*previous)
        close = float(session["c"].iloc[-1])
        broke = ConditionResult(
            label="price broke the first level",
            required=f"close above {levels['r1']:.2f} or below {levels['s1']:.2f}",
            actual=f"close={close:.2f}",
            passed=close > levels["r1"] or close < levels["s1"],
            evaluated=True,
        )
        direction = _crossed_out(session, levels["r1"], levels["s1"])
        if direction is None:
            return RuleDecision.skip("inside the levels, or the break already happened", conditions=[broke])
        return RuleDecision.enter(direction, conditions=[broke], reason="broke the first pivot level")
