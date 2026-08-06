"""Fade rules — bet that a move reverses rather than continues.

These matter for a reason beyond their own merit: a fade and its matching
breakout fire on the SAME bars in opposite directions, so comparing them
isolates whether the entry carries directional information at all. If a rule
loses and its exact inverse also loses, the direction is not the problem —
the cost of the trade is. That comparison is only interpretable when both
sides share their implementation, which is why the fades below wrap the
breakout rules rather than restating them.

Measured on 2026-08-01: ORB-60 lost 0.062 and its fade lost 0.134, while a
random coin flip lost 0.069. Both sides losing, with random in between, is
what "the entry contributes nothing" looks like.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from te.domain.evaluation import ConditionResult
from te.domain.signal import Direction
from te.strategy.indicators import rsi, session_average, stochastic
from te.strategy.rules.breakout import Orb15, Orb60
from te.strategy.session_rule import RuleDecision, SessionRule
from te.strategy.spec import ParamSpec, StrategySpec

_INVERSE: dict[Direction, Direction] = {"long_call": "long_put", "long_put": "long_call"}


class _Inverted(SessionRule):
    """Wraps another rule and flips whichever way it points."""

    #: The rule being inverted. Composed rather than subclassed so the
    #: original's conditions are reported unchanged — the user sees the same
    #: evidence, with the opposite conclusion drawn from it.
    inner: type[SessionRule]

    def __init__(self, params: dict[str, Any] | None = None, *, name: str | None = None) -> None:
        super().__init__(params, name=name)
        # Built ONCE. The inner rule is stateless between calls, but its
        # constructor re-resolves the whole parameter spec (defaults dict,
        # by-name index, per-override clamp) — which, built per minute, ran
        # roughly 225k times per fade strategy over the archive for an
        # object that never differs.
        self._inner = self.inner(self.params)

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        decision = self._inner.decide(session, params)
        if decision.direction is None:
            return decision
        return RuleDecision(
            direction=_INVERSE[decision.direction],
            conditions=decision.conditions,
            reason=f"fading: {decision.reason}",
        )


class Orb15Fade(_Inverted):
    inner = Orb15
    spec = StrategySpec(
        name="orb15_fade",
        family="fade",
        summary="The opposite of the 15-minute breakout — bets the breakout fails and price comes back.",
        params=Orb15.spec.params,
    )


class Orb60Fade(_Inverted):
    inner = Orb60
    spec = StrategySpec(
        name="orb60_fade",
        family="fade",
        summary="The opposite of the one-hour breakout — bets the breakout fails and price comes back.",
        params=Orb60.spec.params,
    )


class _AverageReversion(SessionRule):
    """Price stretched far from the day's average, betting on a snap back.

    The stretch is measured in standard deviations of the rule's OWN recent
    distance from that average, not in points, so "far" means the same thing
    on a quiet day as on a violent one.
    """

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        sigma = float(params["stretch_sigma"])
        average = session_average(session)
        distance = (session["c"] - average) / average
        scale = distance.rolling(60).std().shift(1)
        if pd.isna(scale.iloc[-1]) or float(scale.iloc[-1]) == 0:
            return RuleDecision.skip("not enough bars yet to know what a normal stretch looks like")
        z = float(distance.iloc[-1]) / float(scale.iloc[-1])
        stretched = ConditionResult(
            label="price stretched from the day's average",
            required=f"more than {sigma:.1f} standard deviations away",
            actual=f"{z:+.2f} standard deviations",
            passed=abs(z) > sigma,
            evaluated=True,
        )
        if not stretched.passed:
            return RuleDecision.skip("price is close to its daily average", conditions=[stretched])
        # Stretched ABOVE the average -> bet it falls back -> buy a put.
        return RuleDecision.enter(
            "long_put" if z > 0 else "long_call", conditions=[stretched], reason="stretched far from the daily average"
        )


def _stretch_param(default: float) -> tuple[ParamSpec, ...]:
    return (
        ParamSpec(
            name="stretch_sigma",
            kind="decimal",
            default=default,
            description="How far from the daily average price must go before this acts",
            low=0.5,
            high=5.0,
        ),
    )


class AverageReversion2(_AverageReversion):
    spec = StrategySpec(
        name="twap_reversion_2",
        family="fade",
        summary="Buys when price has fallen far below its average for the day, expecting a bounce.",
        params=_stretch_param(2.0),
    )


class AverageReversion3(_AverageReversion):
    spec = StrategySpec(
        name="twap_reversion_3",
        family="fade",
        summary="Same as the average bounce, but only acts on much bigger stretches.",
        params=_stretch_param(3.0),
    )


class RsiReversion(SessionRule):
    spec = StrategySpec(
        name="rsi_reversion",
        family="fade",
        summary="Buys after price has been falling hard, sells after it has been rising hard.",
        params=(
            ParamSpec(
                name="low", kind="int", default=30, description="Below this counts as 'fallen hard'", low=5, high=45
            ),
            ParamSpec(
                name="high", kind="int", default=70, description="Above this counts as 'risen hard'", low=55, high=95
            ),
        ),
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        # Shifted: the reading that TRIGGERS the entry must be the one that
        # was complete before this bar, not one built partly from the bar
        # being acted on.
        value = rsi(session["c"]).shift(1).iloc[-1]
        if pd.isna(value):
            return RuleDecision.skip("not enough bars to measure momentum yet")
        low, high = int(params["low"]), int(params["high"])
        extreme = ConditionResult(
            label="momentum at an extreme",
            required=f"below {low} (oversold) or above {high} (overbought)",
            actual=f"reading={float(value):.1f}",
            passed=bool(value <= low or value >= high),
            evaluated=True,
        )
        if not extreme.passed:
            return RuleDecision.skip("momentum is in its normal range", conditions=[extreme])
        return RuleDecision.enter(
            "long_call" if value <= low else "long_put", conditions=[extreme], reason="momentum reached an extreme"
        )


class StochasticReversion(SessionRule):
    spec = StrategySpec(
        name="stochastic_reversion",
        family="fade",
        summary="Same bounce idea as the momentum one, measured a different way.",
        params=(
            ParamSpec(name="low", kind="int", default=20, description="Below this counts as low", low=5, high=45),
            ParamSpec(name="high", kind="int", default=80, description="Above this counts as high", low=55, high=95),
        ),
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        value = stochastic(session).shift(1).iloc[-1]
        if pd.isna(value):
            return RuleDecision.skip("not enough bars to measure the range position yet")
        low, high = int(params["low"]), int(params["high"])
        extreme = ConditionResult(
            label="price near the edge of its recent range",
            required=f"below {low} or above {high} (percent of the recent range)",
            actual=f"reading={float(value):.1f}",
            passed=bool(value <= low or value >= high),
            evaluated=True,
        )
        if not extreme.passed:
            return RuleDecision.skip("price sits mid-range", conditions=[extreme])
        return RuleDecision.enter(
            "long_call" if value <= low else "long_put",
            conditions=[extreme],
            reason="price reached the edge of its recent range",
        )


class GapFill(SessionRule):
    spec = StrategySpec(
        name="gap_fill",
        family="fade",
        summary="When the market opens with a jump, bets it comes back to yesterday's close.",
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
        # Acts ONCE, on the first bar at or after the wait — not on every
        # bar for the rest of the day, which would re-enter continuously.
        elapsed = float(session["minutes_from_open"].iloc[-1])
        wait = int(params["act_after_minutes"])
        if not wait <= elapsed < wait + 1:
            return RuleDecision.skip(f"waiting until {wait} minutes after the open", conditions=[gapped])
        return RuleDecision.enter(
            "long_put" if gap_pct > 0 else "long_call", conditions=[gapped], reason="fading the opening jump"
        )
