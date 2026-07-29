"""`MaturityGate` — THE load-bearing file of Phase 6. `influence(p)` is the
**only** place a raw model probability may touch anything resembling a
trading decision, and even there the output is squeezed through
`MLInfluence` — the only type allowed to cross from `te.ml` into
`te.engine.cycle`. Below `gating` stage `influence()` is a structural no-op:
`size_multiplier=1`, `veto=False`, nothing displayed.

Stage is read from the DB (`ml_maturity_state`, a single-row table), never
from a config file or environment variable — promotion is a manual,
audited action (see `scripts/promote_model.py`) that writes both the new
stage and an evidence row to `model_promotions`.

`gating` stage may only veto in **paper** mode, checked against
`engine_state`'s `mode` key (`te.engine.state`'s own physical table — read
here via a locally-declared `sqlalchemy.Core` `Table` matching its schema,
the same pattern `te.ml.trials.TrialLedger` uses to avoid `te.ml` importing
`te.persistence`'s ORM models) — "cannot gate live" per the plan.

`MLHook` is the narrow interface `te.engine.cycle` accepts: it returns only
an `MLInfluence`, never a raw probability, so `cycle.py` can depend on this
module without ever importing `te.ml.model.MetaModel`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from te.persistence.db import session_scope

metadata = sa.MetaData()

#: Single-row (id=1) table holding the current ML maturity stage. Read by
#: `MaturityGate.current_stage()`, written only by `MaturityGate.set_stage()`
#: (called from `scripts/promote_model.py`, a manual/reviewed action).
ml_maturity_state = sa.Table(
    "ml_maturity_state",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("stage", sa.String(16), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

#: Audit trail for every stage promotion/demotion ever made — evidence
#: (DSR/PBO/sample size/etc, serialised as JSON text) is persisted alongside
#: the transition so a promotion can always be independently reviewed later.
model_promotions = sa.Table(
    "model_promotions",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    sa.Column("from_stage", sa.String(16), nullable=False),
    sa.Column("to_stage", sa.String(16), nullable=False),
    sa.Column("actor", sa.String(64), nullable=False),
    sa.Column("criteria_json", sa.Text, nullable=False, default=""),
)

#: Every shadow (or higher-stage) prediction ever made, for future
#: promotion-gate evaluation — logged regardless of stage, but NEVER read by
#: `te.engine.cycle` (it only ever receives the `MLInfluence` this row's `p`
#: was squeezed through).
ml_predictions = sa.Table(
    "ml_predictions",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    sa.Column("cycle_id", sa.Integer, nullable=False),
    sa.Column("instrument", sa.String(64), nullable=False),
    sa.Column("feature_spec_name", sa.String(32), nullable=False),
    sa.Column("feature_spec_version", sa.Integer, nullable=False),
    sa.Column("p", sa.Float, nullable=False),
    sa.Column("stage", sa.String(16), nullable=False),
    sa.Column("displayed", sa.Boolean, nullable=False),
)

#: Read-only view onto `te.engine.state`'s physical `engine_state` table
#: (key/value), declared locally rather than importing
#: `te.persistence.models.EngineState` — see the module docstring.
_engine_state = sa.Table(
    "engine_state",
    sa.MetaData(),
    sa.Column("key", sa.String(64), primary_key=True),
    sa.Column("value", sa.Text, nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

_MODE_KEY = "mode"
_LIVE_MODE = "live"
_VETO_THRESHOLD = 0.5
_ADVISORY_VERDICT_THRESHOLD = 0.5
_LIVE_GATING_MIN_MULTIPLIER = Decimal("0.5")
_LIVE_GATING_MAX_MULTIPLIER = Decimal("1.0")


class Stage(StrEnum):
    SHADOW = "shadow"
    ADVISORY = "advisory"
    GATING = "gating"
    LIVE_GATING = "live-gating"


@dataclass(frozen=True)
class MLInfluence:
    """The ONLY type that may cross from `te.ml` into `te.engine.cycle`'s
    decision path. `size_multiplier` scales a sized position (1 = no
    change); `veto` (when true) means "don't take this trade";derived
    `displayed_verdict` is a human-readable string for the dashboard, or
    `None` when nothing should be shown (shadow stage)."""

    size_multiplier: Decimal
    veto: bool
    displayed_verdict: str | None


class MLHook(Protocol):
    """The narrow interface `te.engine.cycle` depends on. A concrete
    implementation (owning the real `MetaModel`/`FeatureSpec`/`BarStore`)
    lives in `te.ml.predict`; `cycle.py` only ever sees this Protocol and
    `MLInfluence`, never `te.ml.model.MetaModel` and never a raw `p`."""

    def evaluate(self, *, instrument: str, as_of: dt.datetime, cycle_id: int) -> MLInfluence: ...


def _verdict(p: float) -> str:
    return "favourable" if p >= _ADVISORY_VERDICT_THRESHOLD else "unfavourable"


class MaturityGate:
    """Stage is read from the DB via `session_factory`, never from a config
    file. `influence()` is a `match` over `current_stage()` — the whole
    point being that below `gating`, ML cannot touch a decision at all;
    see `tests/ml/test_gates.py::test_ml_cannot_affect_decisions_below_gating`.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        with self._session_factory() as session:
            engine = session.get_bind()
            metadata.create_all(engine, checkfirst=True)

    def current_stage(self) -> Stage:
        with self._session_factory() as session:
            row = session.execute(
                sa.select(ml_maturity_state.c.stage).where(ml_maturity_state.c.id == 1)
            ).scalar_one_or_none()
        if row is None:
            return Stage.SHADOW
        return Stage(row)

    def set_stage(self, stage: Stage, *, actor: str, criteria_json: str = "") -> None:
        """Manual/reviewed promotion (or demotion) — writes the new stage
        AND an audit row to `model_promotions` in the same transaction.
        Never called automatically; `scripts/promote_model.py` is the only
        caller in this phase."""
        now = dt.datetime.now(dt.UTC)
        with session_scope(self._session_factory) as session:
            from_stage = self.current_stage()
            session.execute(
                sa.text(
                    "INSERT INTO ml_maturity_state (id, stage, updated_at) VALUES (1, :stage, :ts) "
                    "ON CONFLICT(id) DO UPDATE SET stage=excluded.stage, updated_at=excluded.updated_at"
                ),
                {"stage": stage.value, "ts": now},
            )
            session.execute(
                model_promotions.insert().values(
                    ts=now, from_stage=from_stage.value, to_stage=stage.value, actor=actor, criteria_json=criteria_json
                )
            )

    def _is_paper_mode(self) -> bool:
        with self._session_factory() as session:
            row = session.execute(
                sa.select(_engine_state.c.value).where(_engine_state.c.key == _MODE_KEY)
            ).scalar_one_or_none()
        return row != _LIVE_MODE

    def influence(self, p: float, *, stage: Stage | None = None) -> MLInfluence:
        """`stage` may be supplied by a caller that has ALREADY read it for
        this same prediction (see `te.ml.predict.ShadowMLHook.evaluate`, which
        also needs it to log the prediction) — the decision is identical
        either way, this only avoids re-querying `ml_maturity_state` twice
        within one prediction."""
        if stage is None:
            stage = self.current_stage()
        if stage is Stage.SHADOW:
            return MLInfluence(Decimal(1), False, None)
        if stage is Stage.ADVISORY:
            return MLInfluence(Decimal(1), False, _verdict(p))
        if stage is Stage.GATING:
            if not self._is_paper_mode():
                # "cannot gate live" — gating stage may only veto in paper mode.
                return MLInfluence(Decimal(1), False, _verdict(p))
            return MLInfluence(Decimal(1), p < _VETO_THRESHOLD, _verdict(p))
        # Stage.LIVE_GATING
        clamped = min(_LIVE_GATING_MAX_MULTIPLIER, max(_LIVE_GATING_MIN_MULTIPLIER, Decimal(str(p))))
        return MLInfluence(clamped, p < _VETO_THRESHOLD, _verdict(p))


def log_prediction(
    session_factory: sessionmaker[Session],
    *,
    cycle_id: int,
    instrument: str,
    feature_spec_name: str,
    feature_spec_version: int,
    p: float,
    stage: Stage,
    displayed: bool,
    ts: dt.datetime,
) -> None:
    """Writes one `ml_predictions` row. Called by an `MLHook` implementation
    (`te.ml.predict`) AFTER computing `MLInfluence` — this is the "predicts
    and logs" half of shadow mode; the row is never read back by
    `te.engine.cycle`."""
    with session_scope(session_factory) as session:
        session.execute(
            ml_predictions.insert().values(
                ts=ts,
                cycle_id=cycle_id,
                instrument=instrument,
                feature_spec_name=feature_spec_name,
                feature_spec_version=feature_spec_version,
                p=p,
                stage=stage.value,
                displayed=displayed,
            )
        )
