"""Mirrors dashboard/src/components/DecisionCard/types.ts."""

from typing import Literal

from pydantic import BaseModel

DecisionVerdict = Literal["traded", "skipped", "error"]


class ConditionResult(BaseModel):
    label: str
    required: str
    actual: str
    passed: bool
    evaluated: bool
