"""`Evaluation` and `ConditionResult` — mirrors
`dashboard/src/types/dashboard-snapshot.ts`'s `CycleEvaluation` and
`dashboard/src/components/DecisionCard/types.ts`'s `ConditionResult` exactly.

The constructor invariant enforced here is safety-critical: the dashboard's
decision-explainability UI depends on `actual` always being a real measured
value, never fabricated or templated. `evaluated=True` requires a non-empty
`actual`, and an unevaluated condition may only carry one of two honest
explanations — there is no way to construct a `ConditionResult` that lies
about whether a condition was actually checked.

There are exactly two ways a condition can go unevaluated, and they mean
different things to whoever reads the decision:

* `"not reached"` — short-circuited by an earlier failed condition. The
  inputs may well have been fine; we simply never got there.
* `"not evaluated — <why>"` — the condition WAS reached, but the
  measurement it needs does not exist. Found live on 2026-07-31:
  `te.strategy.orb`'s volume-confirmation filter compared `0 >= 0` on every
  single evaluation, because an index has no traded volume of its own and
  OpenAlgo returns `volume=0` for `NSE_INDEX`/`BSE_INDEX`. It therefore
  reported `evaluated=True, passed=True` — a green tick, on a filter that
  had never once been applied. Forcing that case to say "not reached" would
  be a second, quieter lie, so it gets its own honest form.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

DecisionVerdict = Literal["traded", "skipped", "error"]

_NOT_REACHED = "not reached"

#: Required prefix for the `actual` of a condition that was reached but
#: could not be measured. The prefix (rather than a free-form string) is what
#: keeps the invariant meaningful: an unevaluated condition still cannot
#: carry something that reads like a real measurement.
NOT_EVALUATED_PREFIX = "not evaluated — "


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
        if not self.evaluated and self.actual != _NOT_REACHED and not self.actual.startswith(NOT_EVALUATED_PREFIX):
            raise ValueError(
                f'evaluated=False requires actual == "{_NOT_REACHED}" (short-circuited) or a '
                f'"{NOT_EVALUATED_PREFIX}<why>" explanation (reached, but unmeasurable), got {self.actual!r}'
            )


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
