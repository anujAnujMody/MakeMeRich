"""`Strategy` — the Protocol every rule (and, in later phases, every
meta-labeled rule) must satisfy. `te.strategy` may never import `te.ml`
(enforced by the `lint-imports` contract "strategy may never import ml") —
this is the structural guarantee that ML cannot generate a signal.
"""

from __future__ import annotations

from typing import Protocol

from te.domain.evaluation import Evaluation
from te.strategy.context import StrategyContext


class Strategy(Protocol):
    name: str

    def evaluate(self, ctx: StrategyContext) -> Evaluation: ...
