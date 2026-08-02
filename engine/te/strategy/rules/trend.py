"""Trend rules — follow the direction price is already moving.

Two of these (`ema_9_21`, `twap_momentum`) are deliberately the crudest
rules in the whole library. They are here as a FLOOR: a sophisticated rule
that cannot beat a moving-average crossover is not earning its complexity,
and without a floor to compare against there is no way to tell.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from te.domain.evaluation import ConditionResult
from te.domain.signal import Direction
from te.strategy.indicators import macd, session_average, supertrend
from te.strategy.session_rule import RuleDecision, SessionRule
from te.strategy.spec import ParamSpec, StrategySpec


def _cross_direction(above: pd.Series) -> Direction | None:
    """Direction if the boolean series flipped on THIS bar.

    Edge-triggered for the same reason the breakouts are: a level-triggered
    "is the fast average above the slow one?" is true for the whole length
    of a trend and would re-enter every single minute of it.
    """
    if len(above) < 2:
        return None
    now, before = bool(above.iloc[-1]), bool(above.iloc[-2])
    if now and not before:
        return "long_call"
    if before and not now:
        return "long_put"
    return None


class _EmaCross(SessionRule):
    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        fast_span, slow_span = int(params["fast"]), int(params["slow"])
        if len(session) < 3:
            return RuleDecision.skip("not enough bars yet")
        fast = session["c"].ewm(span=fast_span, adjust=False).mean()
        slow = session["c"].ewm(span=slow_span, adjust=False).mean()
        direction = _cross_direction(fast > slow)
        crossed = ConditionResult(
            label="fast average crossed the slow one",
            required=f"the {fast_span}-minute average crosses the {slow_span}-minute average on this bar",
            actual=f"fast={float(fast.iloc[-1]):.2f}, slow={float(slow.iloc[-1]):.2f}",
            passed=direction is not None,
            evaluated=True,
        )
        if direction is None:
            return RuleDecision.skip("no crossover on this bar", conditions=[crossed])
        return RuleDecision.enter(direction, conditions=[crossed], reason="moving averages crossed")


def _cross_params(fast: int, slow: int) -> tuple[ParamSpec, ...]:
    return (
        ParamSpec(name="fast", kind="int", default=fast, description="Length of the fast average", low=3, high=60),
        ParamSpec(name="slow", kind="int", default=slow, description="Length of the slow average", low=5, high=200),
    )


class EmaFast(_EmaCross):
    spec = StrategySpec(
        name="ema_9_21",
        family="trend",
        summary="Buys when a short-term average of price crosses above a longer-term one.",
        params=_cross_params(9, 21),
    )


class EmaSlow(_EmaCross):
    spec = StrategySpec(
        name="ema_21_55",
        family="trend",
        summary="The same crossover idea, but slower and less twitchy.",
        params=_cross_params(21, 55),
    )


class MacdCross(SessionRule):
    spec = StrategySpec(
        name="macd_cross",
        family="trend",
        summary="Buys on a popular momentum signal turning upward.",
        params=(
            ParamSpec(name="fast", kind="int", default=12, description="Fast setting", low=3, high=40),
            ParamSpec(name="slow", kind="int", default=26, description="Slow setting", low=10, high=100),
            ParamSpec(name="signal", kind="int", default=9, description="Smoothing setting", low=3, high=30),
        ),
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        if len(session) < 3:
            return RuleDecision.skip("not enough bars yet")
        line, signal_line = macd(
            session["c"], fast=int(params["fast"]), slow=int(params["slow"]), signal=int(params["signal"])
        )
        direction = _cross_direction(line > signal_line)
        crossed = ConditionResult(
            label="momentum line crossed its signal line",
            required="the two lines cross on this bar",
            actual=f"line={float(line.iloc[-1]):+.2f}, signal={float(signal_line.iloc[-1]):+.2f}",
            passed=direction is not None,
            evaluated=True,
        )
        if direction is None:
            return RuleDecision.skip("no crossover on this bar", conditions=[crossed])
        return RuleDecision.enter(direction, conditions=[crossed], reason="momentum lines crossed")


class SuperTrend(SessionRule):
    spec = StrategySpec(
        name="supertrend",
        family="trend",
        summary="Buys when the most widely used trend indicator in India flips to up.",
        params=(
            ParamSpec(
                name="period", kind="int", default=10, description="How many minutes it averages over", low=3, high=60
            ),
            ParamSpec(
                name="multiplier",
                kind="decimal",
                default=3.0,
                description="How far price must move to flip the trend",
                low=1.0,
                high=6.0,
            ),
        ),
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        if len(session) < 20:
            return RuleDecision.skip("not enough bars to establish a trend yet")
        trend = supertrend(session, period=int(params["period"]), multiplier=float(params["multiplier"]))
        direction = _cross_direction(trend > 0)
        flipped = ConditionResult(
            label="trend direction flipped",
            required="the indicator changes from down to up, or up to down, on this bar",
            actual=f"direction={'up' if int(trend.iloc[-1]) > 0 else 'down'}",
            passed=direction is not None,
            evaluated=True,
        )
        if direction is None:
            return RuleDecision.skip("trend direction unchanged on this bar", conditions=[flipped])
        return RuleDecision.enter(direction, conditions=[flipped], reason="trend indicator flipped")


class AverageCross(SessionRule):
    spec = StrategySpec(
        name="twap_momentum",
        family="trend",
        summary="Buys when price crosses above its average price for the day.",
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        del params
        if len(session) < 3:
            return RuleDecision.skip("not enough bars yet")
        average = session_average(session)
        direction = _cross_direction(session["c"] > average)
        crossed = ConditionResult(
            label="price crossed its daily average",
            required="close moves from one side of the daily average to the other",
            actual=f"close={float(session['c'].iloc[-1]):.2f}, average={float(average.iloc[-1]):.2f}",
            passed=direction is not None,
            evaluated=True,
        )
        if direction is None:
            return RuleDecision.skip("price stayed on the same side of its average", conditions=[crossed])
        return RuleDecision.enter(direction, conditions=[crossed], reason="crossed the daily average price")


class FirstHourFollow(SessionRule):
    spec = StrategySpec(
        name="first_hour_momentum",
        family="trend",
        summary="Whichever way the first hour went, follows it.",
        params=(
            ParamSpec(
                name="minutes",
                kind="int",
                default=60,
                description="How much of the morning to measure the direction over",
                low=15,
                high=180,
            ),
        ),
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        minutes = int(params["minutes"])
        elapsed = float(session["minutes_from_open"].iloc[-1])
        # Acts ONCE, on the first bar after the window closes.
        if not minutes <= elapsed < minutes + 1:
            return RuleDecision.skip(f"only acts on the first bar after minute {minutes}")
        opening = session[session["minutes_from_open"] < minutes]
        if len(opening) < 3:
            return RuleDecision.skip("not enough bars in the morning window")
        move = float(opening["c"].iloc[-1]) - float(opening["o"].iloc[0])
        moved = ConditionResult(
            label="the morning had a direction",
            required="the opening window closed away from where it opened",
            actual=f"morning move={move:+.2f} points",
            passed=move != 0,
            evaluated=True,
        )
        if not moved.passed:
            return RuleDecision.skip("the morning finished exactly flat", conditions=[moved])
        return RuleDecision.enter(
            "long_call" if move > 0 else "long_put", conditions=[moved], reason="following the morning's direction"
        )


class GapAndGo(SessionRule):
    spec = StrategySpec(
        name="gap_go",
        family="trend",
        summary="When the market opens with a jump, follows the jump instead of fading it.",
        params=(
            ParamSpec(
                name="min_gap_pct",
                kind="decimal",
                default=0.3,
                description="Ignore jumps smaller than this % of price",
                low=0.05,
                high=3.0,
            ),
            ParamSpec(
                name="act_after_minutes",
                kind="int",
                default=15,
                description="How long to wait after the open before acting",
                low=1,
                high=120,
            ),
        ),
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        previous_close = float(session["previous_close"].iloc[0])
        if pd.isna(previous_close):
            return RuleDecision.skip("no previous session recorded to measure a gap against")
        gap_pct = (float(session["o"].iloc[0]) - previous_close) / previous_close * 100
        gapped = ConditionResult(
            label="market opened with a jump",
            required=f"opening gap larger than {float(params['min_gap_pct']):.2f}%",
            actual=f"gap={gap_pct:+.2f}%",
            passed=abs(gap_pct) >= float(params["min_gap_pct"]),
            evaluated=True,
        )
        if not gapped.passed:
            return RuleDecision.skip("the market opened near yesterday's close", conditions=[gapped])
        elapsed = float(session["minutes_from_open"].iloc[-1])
        wait = int(params["act_after_minutes"])
        if not wait <= elapsed < wait + 1:
            return RuleDecision.skip(f"waiting until {wait} minutes after the open", conditions=[gapped])
        return RuleDecision.enter(
            "long_call" if gap_pct > 0 else "long_put", conditions=[gapped], reason="following the opening jump"
        )
