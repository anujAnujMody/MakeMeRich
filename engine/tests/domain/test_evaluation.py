import datetime as dt

import pytest

from te.domain.evaluation import ConditionResult, Evaluation


def test_evaluated_true_requires_nonempty_actual() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        ConditionResult(label="range width", required="20-200 pts", actual="", passed=True, evaluated=True)


def test_evaluated_true_with_real_actual_is_fine() -> None:
    cond = ConditionResult(label="range width", required="20-200 pts", actual="46.25 pts", passed=True, evaluated=True)
    assert cond.actual == "46.25 pts"


def test_evaluated_false_requires_actual_not_reached() -> None:
    with pytest.raises(ValueError, match="not reached"):
        ConditionResult(label="breakout volume", required=">= 1.5x avg", actual="", passed=False, evaluated=False)


def test_evaluated_false_with_fabricated_actual_rejected() -> None:
    with pytest.raises(ValueError, match="not reached"):
        ConditionResult(
            label="breakout volume",
            required=">= 1.5x avg",
            actual="1.2x avg",
            passed=False,
            evaluated=False,
        )


def test_evaluated_false_with_not_reached_is_fine() -> None:
    cond = ConditionResult(
        label="breakout volume", required=">= 1.5x avg", actual="not reached", passed=False, evaluated=False
    )
    assert cond.actual == "not reached"


def test_evaluated_false_cannot_carry_passed_true() -> None:
    """An unevaluated condition has no honest claim to having passed — that
    is exactly the shape of the live bug where the volume-confirmation
    filter reported `evaluated=False, passed=True`, a green tick on a check
    that never ran. `passed` must be False whenever `evaluated` is False;
    callers read `.outcome` for the real distinction."""
    with pytest.raises(ValueError, match="outcome"):
        ConditionResult(
            label="breakout volume", required=">= 1.5x avg", actual="not reached", passed=True, evaluated=False
        )
    with pytest.raises(ValueError, match="outcome"):
        ConditionResult(
            label="breakout volume",
            required=">= 1.5x avg",
            actual="not evaluated — no volume data",
            passed=True,
            evaluated=False,
        )


def test_evaluation_holds_conditions() -> None:
    cond = ConditionResult(label="range width", required="20-200 pts", actual="46.25 pts", passed=True, evaluated=True)
    evaluation = Evaluation(
        id="cyc-1",
        timestamp=dt.datetime(2026, 7, 29, 9, 30, tzinfo=dt.UTC),
        strategy="orb",
        instrument="NIFTY",
        verdict="skipped",
        reason="volume below threshold",
        conditions=(cond,),
    )
    assert evaluation.conditions == (cond,)
    assert evaluation.verdict == "skipped"
