"""`scripts/promote_model.py::evaluate_gate` — the shadow -> advisory
promotion gate criteria, checked in isolation from any DB/CLI plumbing.
Also covers the Phase 7 additions: `evaluate_advisory_to_gating_gate` and
`evaluate_gating_to_live_gating_gate`, checked against constructed
`SessionRecord` fixtures (no real shadow/advisory data exists yet — this is
infrastructure for later, per the plan's Phase 7 scope note)."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from promote_model import (  # noqa: E402
    SessionRecord,
    evaluate_advisory_to_gating_gate,
    evaluate_gate,
    evaluate_gating_to_live_gating_gate,
    longest_consecutive_gating_run,
)

from te.ml.gates import Stage  # noqa: E402


def _passing_kwargs() -> dict[str, object]:
    return {
        "n_labeled_samples": 250,
        "dsr": 0.97,
        "pbo": 0.02,
        "calibration_slope": 1.0,
        "shuffled_label_control_passed": True,
    }


def test_all_criteria_pass_when_every_gate_is_met() -> None:
    checks = evaluate_gate(**_passing_kwargs())  # type: ignore[arg-type]
    assert all(c.passed for c in checks)


def test_insufficient_samples_fails() -> None:
    kwargs = _passing_kwargs()
    kwargs["n_labeled_samples"] = 199
    checks = evaluate_gate(**kwargs)  # type: ignore[arg-type]
    by_name = {c.name: c.passed for c in checks}
    assert by_name["n_labeled_samples >= 200"] is False


def test_dsr_at_or_below_threshold_fails() -> None:
    kwargs = _passing_kwargs()
    kwargs["dsr"] = 0.95
    checks = evaluate_gate(**kwargs)  # type: ignore[arg-type]
    by_name = {c.name: c.passed for c in checks}
    assert by_name["DSR > 0.95"] is False


def test_pbo_at_or_above_threshold_fails() -> None:
    kwargs = _passing_kwargs()
    kwargs["pbo"] = 0.05
    checks = evaluate_gate(**kwargs)  # type: ignore[arg-type]
    by_name = {c.name: c.passed for c in checks}
    assert by_name["PBO < 0.05"] is False


def test_calibration_slope_out_of_range_fails() -> None:
    kwargs = _passing_kwargs()
    kwargs["calibration_slope"] = 1.3
    checks = evaluate_gate(**kwargs)  # type: ignore[arg-type]
    assert not all(c.passed for c in checks)


def test_missing_calibration_slope_fails() -> None:
    kwargs = _passing_kwargs()
    kwargs["calibration_slope"] = None
    checks = evaluate_gate(**kwargs)  # type: ignore[arg-type]
    assert not all(c.passed for c in checks)


def test_unconfirmed_shuffled_label_control_fails() -> None:
    kwargs = _passing_kwargs()
    kwargs["shuffled_label_control_passed"] = False
    checks = evaluate_gate(**kwargs)  # type: ignore[arg-type]
    by_name = {c.name: c.passed for c in checks}
    assert by_name["shuffled-label control passing"] is False


def _session(
    date: dt.date,
    *,
    stage: Stage = Stage.GATING,
    tier0_clean: bool = True,
    edge_positive: bool = True,
    net_pnl: int = 1,
) -> SessionRecord:
    return SessionRecord(
        date=date,
        stage=stage,
        tier0_clean=tier0_clean,
        model_agreement_edge_positive=edge_positive,
        net_of_cost_pnl_paise=net_pnl,
    )


def _dates(n: int, start: dt.date = dt.date(2026, 1, 1)) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range(n)]


class TestAdvisoryToGatingGate:
    def test_passes_with_30_qualifying_sessions(self) -> None:
        sessions = [_session(d) for d in _dates(30)]
        checks = evaluate_advisory_to_gating_gate(sessions)
        assert all(c.passed for c in checks)

    def test_fails_with_29_qualifying_sessions(self) -> None:
        sessions = [_session(d) for d in _dates(29)]
        checks = evaluate_advisory_to_gating_gate(sessions)
        assert not all(c.passed for c in checks)

    def test_sessions_that_are_not_tier0_clean_dont_count(self) -> None:
        sessions = [_session(d) for d in _dates(30)]
        sessions += [_session(d, tier0_clean=False) for d in _dates(50, start=dt.date(2026, 3, 1))]
        checks = evaluate_advisory_to_gating_gate(sessions)
        assert all(c.passed for c in checks)  # the 30 clean+positive ones are still enough

    def test_sessions_with_negative_edge_dont_count(self) -> None:
        sessions = [_session(d, edge_positive=False) for d in _dates(50)]
        checks = evaluate_advisory_to_gating_gate(sessions)
        assert not all(c.passed for c in checks)


class TestLongestConsecutiveGatingRun:
    def test_all_qualifying_gives_full_length(self) -> None:
        sessions = [_session(d) for d in _dates(60)]
        assert longest_consecutive_gating_run(sessions) == 60

    def test_a_single_bad_session_breaks_the_run(self) -> None:
        sessions = [_session(d) for d in _dates(30)]
        sessions.append(_session(dt.date(2026, 2, 1), net_pnl=-1))
        sessions += [_session(d) for d in _dates(40, start=dt.date(2026, 2, 2))]
        assert longest_consecutive_gating_run(sessions) == 40

    def test_wrong_stage_does_not_qualify(self) -> None:
        sessions = [_session(d, stage=Stage.ADVISORY) for d in _dates(90)]
        assert longest_consecutive_gating_run(sessions) == 0


class TestGatingToLiveGatingGate:
    def test_passes_with_60_consecutive_qualifying_sessions_and_ok_oos_gate(self) -> None:
        sessions = [_session(d) for d in _dates(60)]
        checks = evaluate_gating_to_live_gating_gate(sessions, dsr=0.97, pbo=0.02)
        assert all(c.passed for c in checks)

    def test_fails_with_59_consecutive_qualifying_sessions(self) -> None:
        sessions = [_session(d) for d in _dates(59)]
        checks = evaluate_gating_to_live_gating_gate(sessions, dsr=0.97, pbo=0.02)
        assert not all(c.passed for c in checks)

    def test_fails_when_run_is_long_enough_but_oos_gate_unmet(self) -> None:
        sessions = [_session(d) for d in _dates(60)]
        checks = evaluate_gating_to_live_gating_gate(sessions, dsr=0.80, pbo=0.02)
        by_name = {c.name: c.passed for c in checks}
        assert by_name["strategy DSR > 0.95 (separate OOS gate)"] is False
        assert not all(c.passed for c in checks)

    def test_fails_when_run_broken_by_a_net_negative_session_partway_through(self) -> None:
        sessions = [_session(d) for d in _dates(35)]
        sessions.append(_session(dt.date(2026, 2, 5), net_pnl=-1))
        sessions += [_session(d) for d in _dates(30, start=dt.date(2026, 2, 6))]
        checks = evaluate_gating_to_live_gating_gate(sessions, dsr=0.97, pbo=0.02)
        assert not all(c.passed for c in checks)
