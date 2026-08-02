"""The control — a coin flip.

**This is the most important entry in the library and it must never be
removed.**

Every real strategy loses roughly the same amount, which has two very
different explanations: either they are all individually bad, or none of
them carries any information and what is being measured is the cost of
trading. No t-statistic can separate those two; only a control can.

Measured on 2026-08-01 over 1,182 trades: a coin flip lost 0.069 per trade
while the best real strategy lost 0.062, and at every strike distance and
every exit setting the two tracked each other. That is what "the entry
contributes nothing" looks like, and it is the single most useful number
this project has produced.

So it stays on the Strategies page permanently, beside every other result.
`tradeable=False` keeps it measurable while making it structurally
impossible to trade — the yardstick must never be mistaken for a suggestion.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from te.domain.evaluation import ConditionResult
from te.strategy.session_rule import RuleDecision, SessionRule
from te.strategy.spec import ParamSpec, StrategySpec


class CoinFlip(SessionRule):
    spec = StrategySpec(
        name="random_entry",
        family="control",
        summary="Enters at random, in a random direction. The yardstick every other strategy is measured against.",
        tradeable=False,
        params=(
            ParamSpec(
                name="entries_per_day",
                kind="decimal",
                default=3.0,
                description="Roughly how many random entries to take per day",
                low=0.5,
                high=10.0,
            ),
        ),
    )

    def decide(self, session: pd.DataFrame, params: dict[str, Any]) -> RuleDecision:
        """Decided per MINUTE from a seed of (date, minute) alone.

        Deciding each minute independently — rather than sampling from the
        day's list of candidate minutes — is what makes this causal. An
        earlier draft sampled without replacement from the whole session,
        which made the draw depend on how long the session turned out to be,
        and the no-lookahead test caught it. The control has to meet exactly
        the standard it is used to judge, or the comparison is worthless.

        Deterministic, so a re-run reproduces the same trades.
        """
        elapsed = float(session["minutes_from_open"].iloc[-1])
        if elapsed < 15:
            return RuleDecision.skip("waiting for the first 15 minutes to pass")

        day_seed = int(session["ist"].iloc[0].strftime("%Y%m%d"))
        rng = np.random.default_rng(day_seed * 10_000 + int(elapsed))
        probability = float(params["entries_per_day"]) / 330
        drawn = rng.random()
        fired = drawn < probability
        coin = ConditionResult(
            label="random draw",
            required=f"a uniform draw below {probability:.4f}",
            actual=f"drew {drawn:.4f}",
            passed=fired,
            evaluated=True,
        )
        if not fired:
            return RuleDecision.skip("random draw did not fire this minute", conditions=[coin])
        return RuleDecision.enter(
            "long_call" if rng.random() < 0.5 else "long_put", conditions=[coin], reason="random control entry"
        )
