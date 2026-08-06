"""Timing rules — WHEN to trade rather than WHAT to trade.

Each pairs a simple direction with a strict time window, so what is really
being measured is whether the window itself carries an edge. That question
is worth isolating: an option loses value every minute it is held, so the
part of the day a trade happens in changes its economics more than most
entry signals do.

`MorningPredictsClose` is here even though it has already been tested and
FAILED (2026-08-01, 629 sessions: no relationship, and every coefficient had
the wrong sign for the published effect). A failed strategy that stays
visible with its result attached is worth more than one quietly deleted —
otherwise the same idea gets re-proposed in six months with no record that
it was already checked.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from te.domain.evaluation import ConditionResult
from te.strategy.session_rule import RuleDecision, SessionRule
from te.strategy.spec import ParamSpec, StrategySpec


def _window_condition(elapsed: float, start: int, end: int) -> ConditionResult:
    return ConditionResult(
        label="inside the allowed time window",
        required=f"between minute {start} and {end} after the open",
        actual=f"{elapsed:.0f} minutes since open",
        passed=start <= elapsed <= end,
        evaluated=True,
    )


class _WindowedMomentum(SessionRule):
    """Follows the session's direction so far, but only inside a window, and
    only on the first bar of it."""

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        start, end = int(params["window_start"]), int(params["window_end"])
        elapsed = float(session["minutes_from_open"].iloc[-1])
        window = _window_condition(elapsed, start, end)
        if not window.passed:
            return RuleDecision.skip("outside this strategy's time window", conditions=[window])
        # Fires once, on the first bar inside the window — otherwise it
        # would re-enter every minute the window is open.
        if not start <= elapsed < start + 1:
            return RuleDecision.skip("already past this window's entry bar", conditions=[window])
        move = float(session["c"].iloc[-1]) - float(session["o"].iloc[0])
        moved = ConditionResult(
            label="the session has a direction so far",
            required="price is away from where it opened",
            actual=f"move so far={move:+.2f} points",
            passed=move != 0,
            evaluated=True,
        )
        if not moved.passed:
            return RuleDecision.skip("session is exactly flat", conditions=[window, moved])
        return RuleDecision.enter(
            "long_call" if move > 0 else "long_put",
            conditions=[window, moved],
            reason="entered inside this strategy's time window",
        )


def _window_params(start: int, end: int) -> tuple[ParamSpec, ...]:
    return (
        ParamSpec(
            name="window_start",
            kind="int",
            default=start,
            description="Minutes after the open when this may start trading",
            low=0,
            high=360,
        ),
        ParamSpec(
            name="window_end",
            kind="int",
            default=end,
            description="Minutes after the open when this must stop trading",
            low=0,
            high=375,
        ),
    )


class OpeningWindow(_WindowedMomentum):
    spec = StrategySpec(
        name="first_30_minutes",
        family="timing",
        summary="Only trades in the first 30 minutes, following the session's direction.",
        params=_window_params(30, 60),
    )


class ClosingWindow(_WindowedMomentum):
    spec = StrategySpec(
        name="last_hour",
        family="timing",
        summary="Only trades in the last hour, following the session's direction.",
        params=_window_params(285, 315),
    )


class ExpiryDayOnly(_WindowedMomentum):
    spec = StrategySpec(
        name="expiry_day_only",
        family="timing",
        summary="Only trades on expiry days, when options move fastest.",
        params=_window_params(60, 300),
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        """Expiry days come from the REAL expiry calendar, or the rule stands
        down. There is deliberately no weekday fallback.

        Confirmed against the archive on 2026-08-01: of 125 real NIFTY
        expiries, 83 fell on Thursday and only 32 on Tuesday — the weekday
        moved partway through our data (Thursday -> Wednesday briefly ->
        Tuesday from Sep 2025). A fixed `expiry_weekday` (default Tuesday)
        therefore misclassified the majority of the archive's expiries, and
        the first backtest of this rule (117 trades) was contaminated by
        that — a large share of its "expiry day" trades were ordinary
        Tuesdays, not expiries at all.

        The fallback used to remain for the live path on the grounds that no
        calendar was wired there. That was wrong: the broker's own chain was
        already being fetched on every firing, and
        `OptionContractResolver.expiry_dates` now exposes it. Both paths fill
        `StrategyContext.expiry_dates`, so both get the same
        `is_expiry_day` column.

        With the guess removed, a missing calendar means the rule cannot
        answer its own question — and a rule that cannot tell an expiry from
        an ordinary Tuesday must not trade on the difference.
        """
        today = session["ist"].iloc[0].date()
        if "is_expiry_day" not in session.columns:
            return RuleDecision.skip(
                "no expiry calendar available — refusing to guess from the weekday",
                conditions=[
                    ConditionResult(
                        label="today is an expiry day",
                        required="a real expiry calendar (broker chain live, contract archive in backtest)",
                        actual="no calendar was supplied to this evaluation",
                        passed=False,
                        evaluated=True,
                    )
                ],
            )
        is_expiry = bool(session["is_expiry_day"].iloc[0])
        expiry = ConditionResult(
            label="today is an expiry day",
            required="today is a real expiry date for this underlying",
            actual=f"{today} {'is' if is_expiry else 'is not'} a listed expiry",
            passed=is_expiry,
            evaluated=True,
        )
        if not is_expiry:
            return RuleDecision.skip("not an expiry day", conditions=[expiry])
        inner = super().decide(session, params)
        return RuleDecision(direction=inner.direction, conditions=[expiry, *inner.conditions], reason=inner.reason)


class MorningPredictsClose(SessionRule):
    spec = StrategySpec(
        name="morning_predicts_close",
        family="timing",
        summary="Bets the last 30 minutes go the same way the morning did. Already tested — it does not work.",
        params=(
            ParamSpec(
                name="measure_minutes",
                kind="int",
                default=30,
                description="How much of the morning to measure",
                low=15,
                high=120,
            ),
            ParamSpec(
                name="enter_at_minute",
                kind="int",
                default=345,
                description="Minutes after the open to enter (345 = 3:00pm)",
                low=180,
                high=360,
            ),
        ),
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        enter_at = int(params["enter_at_minute"])
        elapsed = float(session["minutes_from_open"].iloc[-1])
        if not enter_at <= elapsed < enter_at + 1:
            return RuleDecision.skip(f"only enters at minute {enter_at} after the open")
        window = session[session["minutes_from_open"] < int(params["measure_minutes"])]
        if len(window) < 3:
            return RuleDecision.skip("not enough bars in the morning window")
        previous_close = float(session["previous_close"].iloc[0])
        reference = previous_close if not pd.isna(previous_close) else float(window["o"].iloc[0])
        move = float(window["c"].iloc[-1]) - reference
        measured = ConditionResult(
            label="the morning had a direction",
            required="the morning window closed away from the reference price",
            actual=f"morning move={move:+.2f} points",
            passed=move != 0,
            evaluated=True,
        )
        if not measured.passed:
            return RuleDecision.skip("the morning finished exactly flat", conditions=[measured])
        return RuleDecision.enter(
            "long_call" if move > 0 else "long_put",
            conditions=[measured],
            reason="betting the close follows the morning",
        )
