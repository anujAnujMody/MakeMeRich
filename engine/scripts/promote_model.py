#!/usr/bin/env python
"""Manual, reviewed stage-promotion script — reports whether the plan's
shadow -> advisory gate criteria are met for the latest registered model,
and refuses to promote if any are unmet. Never invoked automatically by the
engine; `te.ml.gates.MaturityGate.set_stage()` is the only writer of the
current stage, and this script is the only caller of it outside tests.

Gate (plan's exact wording): "shadow -> advisory needs >= 200 labelled
samples after `params_frozen_at`, DSR>0.95 on CPCV with honest N, PBO<0.05,
calibration slope in [0.8, 1.2], passing shuffled-label control."

`n_labeled_samples`/`dsr`/`pbo`/`calibration_slope` are read straight from
the latest `model_registry` row (`te.ml.registry.get_latest_model_record`),
which is what `te.ml.train.train_meta_model` populates them from. The
shuffled-label control (OOS AUC in [0.45, 0.55]) isn't re-run automatically
by this script — it's a separate, already-covered leakage test
(`tests/leakage/test_shuffled_label_control.py`) — so this script accepts
its pass/fail as an explicit `--shuffled-label-control-passed` flag,
printed as one of the gate's criteria, rather than fabricating a re-run.

Usage:
    python scripts/promote_model.py --model-name orb-secondary \\
        --shuffled-label-control-passed
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from te.ml.gates import MaturityGate, Stage  # noqa: E402
from te.ml.registry import get_latest_model_record  # noqa: E402
from te.persistence.db import engine_from_settings, make_session_factory  # noqa: E402

# The DSR/PBO thresholds and the required gating-session count are ONE
# definition, owned by `te.risk.live_gate` (which enforces them on the
# switch into live money). This script only reports/checks against them —
# it must never drift from the gate that actually blocks real money.
from te.risk.live_gate import MAX_PBO, MIN_DSR  # noqa: E402
from te.risk.live_gate import MIN_GATING_SESSIONS as MIN_GATING_TO_LIVE_GATING_SESSIONS  # noqa: E402
from te.settings import Settings  # noqa: E402

MIN_LABELED_SAMPLES = 200
CALIBRATION_SLOPE_RANGE = (0.8, 1.2)

# Phase 7 additions — advisory -> gating and gating -> live-gating criteria,
# per the plan's "Meta-labeling pipeline" promotion table. These are
# evidence-CHECKING functions only (pure, testable against constructed
# fixture data, per `SessionRecord` below) — there is essentially no real
# shadow/advisory-mode data yet this early in the project, so no DB-reading
# CLI plumbing is built for these two gates in this phase; that's a small,
# mechanical follow-up once real session data exists (mirrors how
# `evaluate_gate`'s DB read is wired below for shadow -> advisory).
MIN_ADVISORY_TO_GATING_SESSIONS = 30


@dataclass(frozen=True)
class GateCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class SessionRecord:
    """One trading session's worth of promotion-evidence facts — the input
    `evaluate_advisory_to_gating_gate`/`evaluate_gating_to_live_gating_gate`
    are checked against. Deliberately NOT an ORM model: this is evidence-
    gathering infrastructure ahead of there being real data to gather, kept
    as a plain fixture-friendly dataclass so it's testable without a DB."""

    date: dt.date
    stage: Stage
    tier0_clean: bool  # True: Tier-0 slippage monitor found no divergence this session
    model_agreement_edge_positive: bool  # secondary model agreeing with primary produced positive net edge
    net_of_cost_pnl_paise: int


def evaluate_advisory_to_gating_gate(sessions: list[SessionRecord]) -> list[GateCheck]:
    """Plan's wording: "advisory -> gating needs >=30 sessions where
    model-agreement edge is positive on Tier-0-clean sessions" — i.e. count
    sessions that are BOTH Tier-0-clean AND have positive model-agreement
    edge; >=30 of those (not merely 30 Tier-0-clean sessions total, and not
    merely 30 positive-edge sessions total)."""
    qualifying = [s for s in sessions if s.tier0_clean and s.model_agreement_edge_positive]
    n = len(qualifying)
    return [
        GateCheck(
            f"tier0-clean sessions with positive model-agreement edge >= {MIN_ADVISORY_TO_GATING_SESSIONS}",
            n >= MIN_ADVISORY_TO_GATING_SESSIONS,
            f"got {n}",
        )
    ]


def longest_consecutive_gating_run(sessions: list[SessionRecord]) -> int:
    """`sessions` in chronological order. Returns the longest run of
    CONSECUTIVE sessions (by list position, not calendar-day arithmetic —
    callers pass only trading sessions) that are simultaneously at `gating`
    stage, net-of-cost-positive, and Tier-0-clean ("slippage within
    model")."""
    best = 0
    current = 0
    for s in sessions:
        qualifies = s.stage is Stage.GATING and s.net_of_cost_pnl_paise > 0 and s.tier0_clean
        current = current + 1 if qualifies else 0
        best = max(best, current)
    return best


def evaluate_gating_to_live_gating_gate(sessions: list[SessionRecord], *, dsr: float, pbo: float) -> list[GateCheck]:
    """Plan's wording: "gating -> live-gating needs >=60 consecutive paper
    sessions at gating with net-of-cost P&L positive, Tier-0 slippage within
    model, AND the strategy's own separate OOS gate (DSR/PBO) satisfied."
    The ML-stage evidence (session run) and the strategy's OOS gate
    (DSR/PBO) are reported as separate checks since the plan calls them out
    as SEPARATE gates that must both hold — mirrors
    `te.ml.gates`' documented "ML gate and the strategy's live-money gate
    are separate" rule."""
    run = longest_consecutive_gating_run(sessions)
    return [
        GateCheck(
            f"consecutive gating sessions (net-positive, Tier-0-clean) >= {MIN_GATING_TO_LIVE_GATING_SESSIONS}",
            run >= MIN_GATING_TO_LIVE_GATING_SESSIONS,
            f"longest run = {run}",
        ),
        GateCheck("strategy DSR > 0.95 (separate OOS gate)", dsr > MIN_DSR, f"got {dsr:.4f}"),
        GateCheck("strategy PBO < 0.05 (separate OOS gate)", pbo < MAX_PBO, f"got {pbo:.4f}"),
    ]


def evaluate_gate(
    *,
    n_labeled_samples: int,
    dsr: float,
    pbo: float,
    calibration_slope: float | None,
    shuffled_label_control_passed: bool,
) -> list[GateCheck]:
    lo, hi = CALIBRATION_SLOPE_RANGE
    slope_ok = calibration_slope is not None and lo <= calibration_slope <= hi
    return [
        GateCheck(
            "n_labeled_samples >= 200",
            n_labeled_samples >= MIN_LABELED_SAMPLES,
            f"got {n_labeled_samples}",
        ),
        GateCheck("DSR > 0.95", dsr > MIN_DSR, f"got {dsr:.4f}"),
        GateCheck("PBO < 0.05", pbo < MAX_PBO, f"got {pbo:.4f}"),
        GateCheck(
            f"calibration slope in [{lo}, {hi}]",
            slope_ok,
            f"got {calibration_slope!r}",
        ),
        GateCheck(
            "shuffled-label control passing",
            shuffled_label_control_passed,
            "confirmed via --shuffled-label-control-passed" if shuffled_label_control_passed else "not confirmed",
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-name", required=True, help="model_registry.name to evaluate")
    parser.add_argument(
        "--shuffled-label-control-passed",
        action="store_true",
        help="confirm tests/leakage/test_shuffled_label_control.py passed for this model's training run",
    )
    parser.add_argument("--actor", default="promote_model.py (manual)")
    args = parser.parse_args()

    settings = Settings()
    engine = engine_from_settings(settings)
    session_factory = make_session_factory(engine)

    record = get_latest_model_record(session_factory, args.model_name)
    if record is None:
        print(f"no model registered under name {args.model_name!r} — nothing to evaluate")
        return 1

    checks = evaluate_gate(
        n_labeled_samples=record.n_labeled_samples,
        dsr=record.dsr,
        pbo=record.pbo,
        calibration_slope=record.calibration_slope,
        shuffled_label_control_passed=args.shuffled_label_control_passed,
    )

    print(f"shadow -> advisory promotion gate for model {args.model_name!r} (registry id={record.id}):")
    all_passed = True
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"  [{status}] {check.name} — {check.detail}")
        all_passed = all_passed and check.passed

    if not all_passed:
        print("\nGate NOT met — refusing to promote.")
        return 1

    gate = MaturityGate(session_factory)
    current = gate.current_stage()
    if current is not Stage.SHADOW:
        print(f"\nGate met, but current stage is {current.value!r}, not 'shadow' — refusing to promote from here.")
        return 1

    gate.set_stage(
        Stage.ADVISORY,
        actor=args.actor,
        criteria_json=str({c.name: {"passed": c.passed, "detail": c.detail} for c in checks}),
    )
    print("\nGate met — promoted shadow -> advisory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
