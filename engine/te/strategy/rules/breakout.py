"""Breakout rules — enter when price escapes a range.

Every rule here is EDGE-triggered: it fires on the bar that CROSSES out of
the range, not on every subsequent bar that merely remains outside. That
distinction is not stylistic. Found live on 2026-07-31: a level-triggered
version asked "is price outside the range right now?", which stays true for
as long as the move lasts, so a single 10:00 breakout that held until 10:30
re-signalled ~30 times, each one opening a fresh position at full round-trip
cost. The strategy literature is uniform that ORB is an entry on the FIRST
close beyond the range.

`_crossed_out` implements that once for all of them.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from te.domain.evaluation import ConditionResult
from te.domain.signal import Direction
from te.strategy.indicators import atr, opening_range, prior_rolling
from te.strategy.session_rule import RuleDecision, SessionRule, not_reached
from te.strategy.spec import ParamSpec, StrategySpec


def _crossed_out(session: pd.DataFrame, high: float, low: float) -> Direction | None:
    """The direction if THIS bar is the first to close beyond `[low, high]`.

    Comparing against the immediately preceding bar rather than remembering
    that we already fired keeps the rule pure and stateless, which matters
    because the registry hands out a fresh instance per evaluation. It also
    makes a genuine SECOND breakout tradeable: if price falls back inside
    and later breaks out again, that is a real new crossing.
    """
    if len(session) < 2:
        return None
    close = float(session["c"].iloc[-1])
    previous = float(session["c"].iloc[-2])
    if not (low <= previous <= high):
        return None  # the crossing already happened on an earlier bar
    if close > high:
        return "long_call"
    if close < low:
        return "long_put"
    return None


def _range_condition(close: float, high: float, low: float, label: str) -> ConditionResult:
    return ConditionResult(
        label=label,
        required=f"a bar CROSSING out: close > {high:.2f} or close < {low:.2f}, previous close inside",
        actual=f"close={close:.2f}, range=[{low:.2f}, {high:.2f}]",
        passed=not (low <= close <= high),
        evaluated=True,
    )


class _OpeningRangeBreakout(SessionRule):
    """Shared body for the three opening-range lengths."""

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        minutes = int(params["opening_range_minutes"])
        bounds = opening_range(session, minutes)
        formed = ConditionResult(
            label="opening range formed",
            required=f"the first {minutes} minutes of the session have completed",
            actual=f"{float(session['minutes_from_open'].iloc[-1]):.0f} minutes since open",
            passed=bounds is not None,
            evaluated=True,
        )
        if bounds is None:
            return RuleDecision.skip(
                "opening range not yet formed", conditions=[formed, not_reached("breakout beyond opening range")]
            )
        high, low = bounds
        close = float(session["c"].iloc[-1])
        breakout = _range_condition(close, high, low, "breakout beyond opening range")
        direction = _crossed_out(session, high, low)
        if direction is None:
            reason = (
                "already beyond the opening range — the crossing happened on an earlier bar"
                if breakout.passed
                else "close finished inside the opening range — no breakout"
            )
            return RuleDecision.skip(reason, conditions=[formed, breakout])
        return RuleDecision.enter(
            direction, conditions=[formed, breakout], reason=f"closed beyond the {minutes}-minute opening range"
        )


def _orb_params(default_minutes: int) -> tuple[ParamSpec, ...]:
    return (
        ParamSpec(
            name="opening_range_minutes",
            kind="int",
            default=default_minutes,
            description="How many minutes after the open to measure the range over",
            low=5,
            high=120,
        ),
    )


class Orb15(_OpeningRangeBreakout):
    spec = StrategySpec(
        name="orb15",
        family="breakout",
        summary="Buys when price breaks out of the range it traded in the first 15 minutes.",
        params=_orb_params(15),
    )


class Orb30(_OpeningRangeBreakout):
    spec = StrategySpec(
        name="orb30",
        family="breakout",
        summary="Buys when price breaks out of the range it traded in the first 30 minutes.",
        params=_orb_params(30),
    )


class Orb60(_OpeningRangeBreakout):
    spec = StrategySpec(
        name="orb60",
        family="breakout",
        summary="Buys when price breaks out of the range it traded in the first hour.",
        params=_orb_params(60),
    )


class TightRangeBreakout(_OpeningRangeBreakout):
    spec = StrategySpec(
        name="tight_range_breakout",
        family="breakout",
        summary="Only takes the morning breakout when the opening range was unusually narrow.",
        params=(
            *_orb_params(60),
            ParamSpec(
                name="max_width_pct",
                kind="decimal",
                default=0.35,
                description="Skip the day unless the opening range is narrower than this % of price",
                low=0.05,
                high=2.0,
            ),
        ),
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        """A breakout, but only out of an unusually tight range.

        The premise worth testing: a move's size should scale with the
        coiling that preceded it, so a tight range is the setup most likely
        to produce a move fast enough to outrun an option's time decay.
        """
        bounds = opening_range(session, int(params["opening_range_minutes"]))
        if bounds is None:
            return RuleDecision.skip(
                "opening range not yet formed", conditions=[not_reached("opening range narrow enough")]
            )
        high, low = bounds
        width_pct = (high - low) / low * 100
        tight = ConditionResult(
            label="opening range narrow enough",
            required=f"range width < {float(params['max_width_pct']):.2f}% of price",
            actual=f"width={width_pct:.3f}%",
            passed=width_pct < float(params["max_width_pct"]),
            evaluated=True,
        )
        if not tight.passed:
            return RuleDecision.skip(
                "opening range too wide — no coiling to expand from",
                conditions=[tight, not_reached("breakout beyond opening range")],
            )
        inner = super().decide(session, params)
        return RuleDecision(
            direction=inner.direction,
            conditions=[tight, *inner.conditions],
            reason=inner.reason,
        )


class _Donchian(SessionRule):
    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        window = int(params["window"])
        highs = prior_rolling(session["h"], window, "max")
        lows = prior_rolling(session["l"], window, "min")
        if pd.isna(highs.iloc[-1]) or pd.isna(lows.iloc[-1]):
            return RuleDecision.skip(f"fewer than {window} bars so far today")
        high, low = float(highs.iloc[-1]), float(lows.iloc[-1])
        close = float(session["c"].iloc[-1])
        breakout = _range_condition(close, high, low, f"breaks the {window}-bar high or low")
        if not breakout.passed:
            return RuleDecision.skip(f"inside the last {window} bars' range", conditions=[breakout])
        direction: Direction = "long_call" if close > high else "long_put"
        return RuleDecision.enter(direction, conditions=[breakout], reason=f"broke the {window}-bar range")


class Donchian20(_Donchian):
    spec = StrategySpec(
        name="donchian_20",
        family="breakout",
        summary="Buys when price rises above the highest point of the last 20 minutes.",
        params=(
            ParamSpec(
                name="window",
                kind="int",
                default=20,
                description="How many recent minutes the high and low are measured over",
                low=5,
                high=240,
            ),
        ),
    )


class Donchian60(_Donchian):
    spec = StrategySpec(
        name="donchian_60",
        family="breakout",
        summary="Buys when price rises above the highest point of the last hour.",
        params=(
            ParamSpec(
                name="window",
                kind="int",
                default=60,
                description="How many recent minutes the high and low are measured over",
                low=5,
                high=240,
            ),
        ),
    )


class PreviousDayLevels(SessionRule):
    spec = StrategySpec(
        name="previous_day_breakout",
        family="breakout",
        summary="Buys when price breaks above yesterday's high, or below yesterday's low.",
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        del params
        high = float(session["previous_high"].iloc[0])
        low = float(session["previous_low"].iloc[0])
        if pd.isna(high) or pd.isna(low):
            return RuleDecision.skip("no previous session recorded to take levels from")
        close = float(session["c"].iloc[-1])
        breakout = _range_condition(close, high, low, "breaks yesterday's high or low")
        direction = _crossed_out(session, high, low)
        if direction is None:
            return RuleDecision.skip(
                "inside yesterday's range, or the crossing already happened", conditions=[breakout]
            )
        return RuleDecision.enter(direction, conditions=[breakout], reason="broke yesterday's range")


class BollingerSqueeze(SessionRule):
    spec = StrategySpec(
        name="bollinger_squeeze",
        family="breakout",
        summary="Buys when price has been unusually quiet and then bursts out of that quiet band.",
        params=(
            ParamSpec(
                name="window", kind="int", default=20, description="Minutes used to measure the band", low=10, high=120
            ),
            ParamSpec(
                name="band_stdev",
                kind="decimal",
                default=2.0,
                description="How many standard deviations wide the band is",
                low=1.0,
                high=4.0,
            ),
        ),
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        window = int(params["window"])
        mean = prior_rolling(session["c"], window, "mean")
        stdev = prior_rolling(session["c"], window, "std")
        if pd.isna(mean.iloc[-1]) or pd.isna(stdev.iloc[-1]) or float(stdev.iloc[-1]) == 0:
            return RuleDecision.skip(f"fewer than {window} bars so far today")
        upper = float(mean.iloc[-1]) + float(params["band_stdev"]) * float(stdev.iloc[-1])
        lower = float(mean.iloc[-1]) - float(params["band_stdev"]) * float(stdev.iloc[-1])
        close = float(session["c"].iloc[-1])
        breakout = _range_condition(close, upper, lower, "closes outside the quiet band")
        direction = _crossed_out(session, upper, lower)
        if direction is None:
            return RuleDecision.skip("inside the band, or already outside it", conditions=[breakout])
        return RuleDecision.enter(direction, conditions=[breakout], reason="burst out of a quiet band")


class AtrExpansion(SessionRule):
    spec = StrategySpec(
        name="atr_expansion",
        family="breakout",
        summary="Buys when a single minute moves much further than the recent normal.",
        params=(
            ParamSpec(
                name="multiple",
                kind="decimal",
                default=2.0,
                description="How many times the normal move size counts as a burst",
                low=1.0,
                high=6.0,
            ),
        ),
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        normal = atr(session, 14)
        if pd.isna(normal.iloc[-1]) or float(normal.iloc[-1]) == 0:
            return RuleDecision.skip("not enough bars to measure a normal move size yet")
        bar = session.iloc[-1]
        move = float(bar["c"]) - float(bar["o"])
        threshold = float(params["multiple"]) * float(normal.iloc[-1])
        expanded = ConditionResult(
            label="this minute moved unusually far",
            required=f"|move| > {threshold:.2f} points ({float(params['multiple']):.1f}x the recent normal)",
            actual=f"move={move:+.2f} points",
            passed=abs(move) > threshold,
            evaluated=True,
        )
        if not expanded.passed:
            return RuleDecision.skip("this minute moved a normal amount", conditions=[expanded])
        return RuleDecision.enter(
            "long_call" if move > 0 else "long_put", conditions=[expanded], reason="range expanded sharply"
        )


class InsideBarBreakout(SessionRule):
    spec = StrategySpec(
        name="inside_bar",
        family="breakout",
        summary="Buys when a quiet minute sits inside the previous one, then price breaks out of it.",
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        del params
        if len(session) < 3:
            return RuleDecision.skip("need at least three bars to find an inside bar")
        parent, inside, current = session.iloc[-3], session.iloc[-2], session.iloc[-1]
        is_inside = float(inside["h"]) <= float(parent["h"]) and float(inside["l"]) >= float(parent["l"])
        pattern = ConditionResult(
            label="previous minute sat inside the one before it",
            required="previous high <= earlier high AND previous low >= earlier low",
            actual=(
                f"previous=[{float(inside['l']):.2f}, {float(inside['h']):.2f}] "
                f"inside [{float(parent['l']):.2f}, {float(parent['h']):.2f}]"
            ),
            passed=is_inside,
            evaluated=True,
        )
        if not is_inside:
            return RuleDecision.skip(
                "no inside bar to break out of", conditions=[pattern, not_reached("price breaks the inside bar")]
            )
        close = float(current["c"])
        breakout = _range_condition(close, float(inside["h"]), float(inside["l"]), "price breaks the inside bar")
        if not breakout.passed:
            return RuleDecision.skip("still inside the quiet bar's range", conditions=[pattern, breakout])
        return RuleDecision.enter(
            "long_call" if close > float(inside["h"]) else "long_put",
            conditions=[pattern, breakout],
            reason="broke out of an inside bar",
        )
