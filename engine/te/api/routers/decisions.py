import datetime as dt

from fastapi import APIRouter, Response

from te.api.db import session_factory
from te.api.provenance import set_provenance
from te.api.schemas.dashboard import CycleEvaluation
from te.api.schemas.decision import ConditionOutcome, ConditionResult
from te.domain.clock import IST
from te.domain.evaluation import NOT_EVALUATED_PREFIX
from te.persistence.models import EvaluationConditionRow
from te.persistence.repos.paper_trading import conditions_for, evaluations_today

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


def _outcome(row: EvaluationConditionRow) -> ConditionOutcome:
    """Same rule as `te.domain.evaluation.ConditionResult.outcome`, applied
    to the persisted row (which carries the stored booleans and text, not the
    domain object)."""
    if row.evaluated:
        return "passed" if row.passed else "failed"
    return "unmeasurable" if row.actual.startswith(NOT_EVALUATED_PREFIX) else "not_reached"


@router.get("/today", response_model=list[CycleEvaluation])
def get_today_decisions(response: Response) -> list[CycleEvaluation]:
    """Every condition-by-condition strategy evaluation recorded today, from
    the real `cycle_evaluations`/`evaluation_conditions` tables —
    `run_entry_cycle` writes one row per (strategy, instrument) every single
    cycle regardless of verdict (traded/skipped), so an empty result here
    means no cycle has evaluated anything yet today, not that the endpoint
    is unwired."""
    with session_factory() as session:
        today = dt.datetime.now(IST).date()
        rows = evaluations_today(session, today)
        result = [
            CycleEvaluation(
                id=row.evaluation_id,
                timestamp=row.ts.isoformat(),
                strategy=row.strategy,
                instrument=row.instrument,
                verdict=row.verdict,
                reason=row.reason,
                conditions=[
                    ConditionResult(
                        label=c.label,
                        required=c.required,
                        actual=c.actual,
                        passed=c.passed,
                        evaluated=c.evaluated,
                        outcome=_outcome(c),
                    )
                    for c in conditions_for(session, row.evaluation_id)
                ],
            )
            for row in rows
        ]
    set_provenance(response, provenance="paper" if result else "none", sample_size=len(result))
    return result
