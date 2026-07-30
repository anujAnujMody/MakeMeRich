import datetime as dt

from fastapi import APIRouter, Response

from te.api.db import session_factory
from te.api.provenance import set_provenance
from te.api.schemas.dashboard import CycleEvaluation
from te.api.schemas.decision import ConditionResult
from te.domain.clock import IST
from te.persistence.repos.paper_trading import conditions_for, evaluations_today

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


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
                    )
                    for c in conditions_for(session, row.evaluation_id)
                ],
            )
            for row in rows
        ]
    set_provenance(response, provenance="paper" if result else "none", sample_size=len(result))
    return result
