"""`Evaluation` and `ConditionResult` — mirrors
`dashboard/src/types/dashboard-snapshot.ts`'s `CycleEvaluation` and
`dashboard/src/components/DecisionCard/types.ts`'s `ConditionResult` exactly.

The constructor invariant enforced here is safety-critical: the dashboard's
decision-explainability UI depends on `actual` always being a real measured
value, never fabricated or templated. `evaluated=True` requires a non-empty
`actual`; `evaluated=False` requires `actual == "not reached"` — there is no
way to construct a `ConditionResult` that lies about whether a condition was
actually checked.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

DecisionVerdict = Literal["traded", "skipped", "error"]

_NOT_REACHED = "not reached"


@dataclass(frozen=True)
class ConditionResult:
    """One condition inside a strategy's evaluation, e.g. "range width
    20-200 pts". `label`/`required` are human-readable; `actual` is the real
    measured value at evaluation time, never approximated."""

    label: str
    required: str
    actual: str
    passed: bool
    evaluated: bool

    def __post_init__(self) -> None:
        if self.evaluated and not self.actual:
            raise ValueError("evaluated=True requires a non-empty `actual` value — never fabricate or omit it")
        if not self.evaluated and self.actual != _NOT_REACHED:
            raise ValueError(f'evaluated=False requires actual == "not reached", got {self.actual!r}')


@dataclass(frozen=True)
class Evaluation:
    """A single strategy evaluation for one cycle × instrument, mirroring
    `CycleEvaluation` in the dashboard's TS types."""

    id: str
    timestamp: dt.datetime
    strategy: str
    instrument: str
    verdict: DecisionVerdict
    reason: str
    conditions: tuple[ConditionResult, ...]
