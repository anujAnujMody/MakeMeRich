"""Mirrors dashboard/src/components/DecisionCard/types.ts."""

from typing import Literal

from pydantic import BaseModel

DecisionVerdict = Literal["traded", "skipped", "error"]
ConditionOutcome = Literal["passed", "failed", "not_reached", "unmeasurable"]


class ConditionResult(BaseModel):
    label: str
    required: str
    actual: str
    passed: bool
    evaluated: bool
    #: The four states explicitly, so the dashboard never has to collapse
    #: "not reached" and "could not be measured" into one — it rendered both
    #: identically before, which mislabelled a condition that HAD run.
    outcome: ConditionOutcome
