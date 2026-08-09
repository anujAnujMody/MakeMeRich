"""`LiveUnlockGate` — the Phase 8 live-money unlock gate. `POST /api/mode
{mode:'live'}` calls `check()` first (see `te/api/routers/mode.py`) and
refuses the switch with **HTTP 409** unless every condition below holds,
checked against REAL persisted data — never fabricated, never assumed true:

- strategy DSR > 0.95 with honest N, and PBO < 0.05 — read from the latest
  `model_registry` row for the strategy (`te.ml.registry`), which is exactly
  where `te.ml.train.train_meta_model` persists the DSR/PBO it computed via
  `te.ml.metrics.deflated_sharpe_ratio()`/`probability_of_backtest_overfitting()`
  using the honest trial count from `te.ml.trials.TrialLedger` — this module
  does not recompute either statistic, only reads the already-honest result.
- >= 90 sessions of net-positive paper P&L strictly AFTER
  `te.engine.state.get_params_frozen_at()` — "paper is OOS" only holds once
  params are frozen (plan's R5); a session is one distinct calendar day of
  CLOSED paper trades whose net P&L for that day sums positive.
- Tier-0 slippage currently clean (`te.risk.monitors.SlippageMonitor`, not
  breached) — "within model" — AND measured on a real sample: at least
  `MIN_SLIPPAGE_OBSERVATIONS`, with non-zero spread. Both extra clauses are
  load-bearing. `breached` is `False` on an empty sample, so the original
  `if status.breached` passed trivially on zero observations; and once fills
  were actually recorded, every PAPER observation is exactly zero (the
  `SimulatedBroker` fills at precisely the limit price), which drives
  `stdev` to 0, `z_score` to `None`, and `breached` back to `False`. Cleanly
  measured simulated execution is not evidence about live execution, and
  this gate must not be satisfiable without ever touching a real venue.
- ML stage is `gating` (`te.ml.gates.MaturityGate`) AND has been for >= 60
  sessions, anchored at the most recent `model_promotions` row promoting
  INTO `gating` (a session here is one distinct calendar day with at least
  one recorded paper-mode cycle in `cycles`, matching Tier 3's own session
  counting convention in `te.risk.monitors.RollingPerformance`).

With essentially no real trading history yet, `check()` currently returns
`passed=False` with several honest failing conditions — that is the CORRECT
state of the project right now, not a bug to route around (mirrors
`scripts/promote_model.py`'s refusal to promote on unmet evidence).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from te.engine.state import get_params_frozen_at
from te.ml.gates import MaturityGate, Stage
from te.ml.registry import get_latest_model_record
from te.persistence.models import CycleRow, ModelPromotionRow, TradeRow
from te.risk.monitors import SlippageMonitor

MIN_DSR = 0.95
MAX_PBO = 0.05
MIN_POST_FREEZE_SESSIONS = 90
MIN_GATING_SESSIONS = 60

#: Slippage observations required before "execution is within model" may be
#: asserted at all. Without a floor the condition read `if status.breached`,
#: and `SlippageStatus.breached` is `False` on an EMPTY sample — so the one
#: condition guarding real-money execution quality passed by having measured
#: nothing. It had never observed anything, because nothing in production
#: called `ExecutionManager.on_fill` (fixed alongside this, see
#: `ExecutionManager.drain_fills`).
MIN_SLIPPAGE_OBSERVATIONS = 30


@dataclass(frozen=True)
class GateCheckResult:
    """`failing_conditions` are human-readable and specific (a real measured
    value against a real threshold, e.g. "DSR 0.62 < required 0.95") — never
    a generic "not ready", per the same never-a-silent-rejection discipline
    as Phase 4's sizing rejections."""

    passed: bool
    failing_conditions: tuple[str, ...]


def _post_freeze_positive_sessions(session: Session, *, strategy: str, since: dt.datetime) -> int:
    """Distinct calendar days (by `closed_at.date()`) of CLOSED paper trades
    for `strategy`, strictly after `since`, whose net P&L for that day sums
    positive."""
    rows = (
        session.execute(
            sa.select(TradeRow).where(
                TradeRow.mode == "paper",
                TradeRow.strategy == strategy,
                TradeRow.closed_at > since,
            )
        )
        .scalars()
        .all()
    )
    by_date: dict[dt.date, int] = {}
    for row in rows:
        d = row.closed_at.date()
        by_date[d] = by_date.get(d, 0) + row.net_pnl_paise
    return sum(1 for net in by_date.values() if net > 0)


def _paper_sessions_since(session: Session, *, since: dt.datetime) -> int:
    """Distinct calendar days with at least one recorded paper-mode `cycles`
    row strictly after `since` — how long the current ML stage has actually
    been running for, not merely how long ago it was promoted."""
    rows = (
        session.execute(sa.select(CycleRow.ts).where(CycleRow.mode == "paper", CycleRow.ts > since)).scalars().all()
    )
    return len({ts.date() for ts in rows})


def _latest_promotion_to(session: Session, stage: Stage) -> dt.datetime | None:
    row = session.execute(
        sa.select(ModelPromotionRow.ts)
        .where(ModelPromotionRow.to_stage == stage.value)
        .order_by(ModelPromotionRow.ts.desc())
    ).first()
    return row[0] if row is not None else None


class LiveUnlockGate:
    """`check()` reads every condition fresh from the DB on every call — no
    caching, no assumed-true defaults."""

    def __init__(
        self, session_factory: sessionmaker[Session], *, strategy: str = "orb", instrument: str = "NIFTY"
    ) -> None:
        self._session_factory = session_factory
        self._strategy = strategy
        self._instrument = instrument

    def check(self) -> GateCheckResult:
        failing: list[str] = []

        record = get_latest_model_record(self._session_factory, self._strategy)
        if record is None:
            failing.append(f"no DSR recorded yet for strategy {self._strategy!r}")
            failing.append(f"no PBO recorded yet for strategy {self._strategy!r}")
        else:
            if not record.dsr > MIN_DSR:
                failing.append(
                    f"DSR {record.dsr:.4f} < required {MIN_DSR} for strategy {self._strategy!r} "
                    f"(n_trials={record.n_trials_at_training})"
                )
            if not record.pbo < MAX_PBO:
                failing.append(
                    f"PBO {record.pbo:.4f} >= required max {MAX_PBO} for strategy {self._strategy!r}"
                )

        stage = MaturityGate(self._session_factory).current_stage()

        with self._session_factory() as session:
            frozen_at = get_params_frozen_at(session)
            if frozen_at is None:
                failing.append(
                    f"params_frozen_at has not been set for strategy {self._strategy!r} — "
                    "cannot count post-freeze paper sessions"
                )
            else:
                n_sessions = _post_freeze_positive_sessions(session, strategy=self._strategy, since=frozen_at)
                if n_sessions < MIN_POST_FREEZE_SESSIONS:
                    failing.append(
                        f"{n_sessions} of {MIN_POST_FREEZE_SESSIONS} required post-freeze net-positive "
                        f"paper sessions completed for strategy {self._strategy!r} "
                        f"(frozen at {frozen_at.isoformat()})"
                    )

            status = SlippageMonitor(session, instrument=self._instrument).status()
            z_display = f"{status.z_score:.2f}" if status.z_score is not None else "n/a"
            if status.breached:
                failing.append(
                    f"Tier-0 slippage monitor is currently breached for {self._instrument!r}: "
                    f"mean divergence {status.mean_diff_paise:.2f}p over {status.n} observations "
                    f"(z={z_display})"
                )
            elif status.n < MIN_SLIPPAGE_OBSERVATIONS:
                failing.append(
                    f"only {status.n} of {MIN_SLIPPAGE_OBSERVATIONS} required slippage observations "
                    f"recorded for {self._instrument!r} — 'execution is within model' cannot be "
                    "asserted from a sample this small"
                )
            elif status.stdev_diff_paise <= 0:
                failing.append(
                    f"all {status.n} slippage observations for {self._instrument!r} are identical "
                    f"(stdev 0) — that is a simulated fill signature, not measured execution. "
                    "SimulatedBroker fills every order at exactly its limit price, so paper fills "
                    "carry no evidence about live execution quality"
                )

            if stage is not Stage.GATING:
                failing.append(
                    f"ML stage is {stage.value!r}, required 'gating' for >= {MIN_GATING_SESSIONS} sessions"
                )
            else:
                gating_since = _latest_promotion_to(session, Stage.GATING)
                if gating_since is None:
                    failing.append(
                        "ML stage is 'gating' but no model_promotions row records when it was "
                        "promoted — cannot verify session duration"
                    )
                else:
                    n_gating_sessions = _paper_sessions_since(session, since=gating_since)
                    if n_gating_sessions < MIN_GATING_SESSIONS:
                        failing.append(
                            f"ML stage 'gating' has only run for {n_gating_sessions} of "
                            f"{MIN_GATING_SESSIONS} required sessions (promoted {gating_since.isoformat()})"
                        )

        return GateCheckResult(passed=not failing, failing_conditions=tuple(failing))


__all__ = [
    "MAX_PBO",
    "MIN_DSR",
    "MIN_GATING_SESSIONS",
    "MIN_POST_FREEZE_SESSIONS",
    "MIN_SLIPPAGE_OBSERVATIONS",
    "GateCheckResult",
    "LiveUnlockGate",
]
