"""`model_registry` — persists trained model artifacts + provenance (feature
spec version, DSR, PBO, honest trial count at training time, calibration
info). This is what `scripts/promote_model.py` reads to decide whether a
model has earned promotion, and what a later phase's `/api/learning/*`
endpoints read for the dashboard's Learn page.

Declared as a raw `sqlalchemy.Core` `Table` (same convention as
`te.ml.trials.TrialLedger`/`te.ml.gates`) so `te.ml` doesn't need to import
`te.persistence`'s ORM models; the caller supplies a `sessionmaker[Session]`.
"""

from __future__ import annotations

import datetime as dt
import pickle
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from te.ml.model import MetaModel
from te.persistence.db import session_scope
from te.sqltypes import UtcDateTime

metadata = sa.MetaData()

model_registry = sa.Table(
    "model_registry",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("name", sa.String(64), nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("feature_spec_name", sa.String(32), nullable=False),
    sa.Column("feature_spec_version", sa.Integer, nullable=False),
    sa.Column("artifact_blob", sa.LargeBinary, nullable=False),
    sa.Column("gbm_library", sa.String(16), nullable=False),
    sa.Column("calibration_method", sa.String(16), nullable=False),
    sa.Column("calibration_slope", sa.Float, nullable=True),
    sa.Column("dsr", sa.Float, nullable=False),
    sa.Column("pbo", sa.Float, nullable=False),
    sa.Column("n_trials_at_training", sa.Integer, nullable=False),
    sa.Column("n_labeled_samples", sa.Integer, nullable=False),
    sa.Column("created_at", UtcDateTime, nullable=False),
    sa.Column("notes", sa.Text, nullable=False, default=""),
)


@dataclass(frozen=True)
class ModelRecord:
    id: int
    name: str
    version: int
    feature_spec_name: str
    feature_spec_version: int
    gbm_library: str
    calibration_method: str
    calibration_slope: float | None
    dsr: float
    pbo: float
    n_trials_at_training: int
    n_labeled_samples: int
    created_at: dt.datetime
    notes: str


def register_model(
    session_factory: sessionmaker[Session],
    *,
    model: MetaModel,
    name: str,
    version: int,
    gbm_library: str,
    calibration_method: str = "sigmoid",
    calibration_slope: float | None,
    dsr: float,
    pbo: float,
    n_trials_at_training: int,
    n_labeled_samples: int,
    notes: str = "",
) -> int:
    """Persists one trained+calibrated `MetaModel` artifact and its
    provenance. Returns the new row's `id`."""
    artifact_blob = pickle.dumps(model)
    with session_scope(session_factory) as session:
        engine = session.get_bind()
        metadata.create_all(engine, checkfirst=True)
        result = session.execute(
            model_registry.insert().values(
                name=name,
                version=version,
                feature_spec_name=model.feature_spec.name,
                feature_spec_version=model.feature_spec.version,
                artifact_blob=artifact_blob,
                gbm_library=gbm_library,
                calibration_method=calibration_method,
                calibration_slope=calibration_slope,
                dsr=dsr,
                pbo=pbo,
                n_trials_at_training=n_trials_at_training,
                n_labeled_samples=n_labeled_samples,
                created_at=dt.datetime.now(dt.UTC),
                notes=notes,
            )
        )
        # `Session.execute()` is typed to return the base `Result[Any]`,
        # which doesn't expose `inserted_primary_key` (a `CursorResult`-only
        # attribute) — it's present at runtime for a Core INSERT executed
        # through a Session, same as `te.ml.trials.TrialLedger.record()`'s
        # `Connection.execute()` call. Read from the cursor BEFORE
        # `session_scope` commits on exit.
        inserted_id = result.inserted_primary_key  # type: ignore[attr-defined]
    assert inserted_id is not None
    return int(inserted_id[0])


def _row_to_record(row: sa.Row) -> ModelRecord:  # type: ignore[type-arg]
    return ModelRecord(
        id=row.id,
        name=row.name,
        version=row.version,
        feature_spec_name=row.feature_spec_name,
        feature_spec_version=row.feature_spec_version,
        gbm_library=row.gbm_library,
        calibration_method=row.calibration_method,
        calibration_slope=row.calibration_slope,
        dsr=row.dsr,
        pbo=row.pbo,
        n_trials_at_training=row.n_trials_at_training,
        n_labeled_samples=row.n_labeled_samples,
        created_at=row.created_at,
        notes=row.notes,
    )


#: Every `model_registry` column EXCEPT `artifact_blob` — a pickled model is
#: ~half a megabyte, and no metadata read path ever looks at it. Selecting
#: `model_registry` wholesale would drag the blob off disk on every gate
#: check (`te.risk.live_gate`, `scripts/promote_model.py`).
_METADATA_COLUMNS = tuple(c for c in model_registry.c if c.name != "artifact_blob")


def get_latest_model_record(session_factory: sessionmaker[Session], name: str) -> ModelRecord | None:
    with session_factory() as session:
        engine = session.get_bind()
        metadata.create_all(engine, checkfirst=True)
        row = session.execute(
            sa.select(*_METADATA_COLUMNS).where(model_registry.c.name == name).order_by(model_registry.c.id.desc())
        ).first()
    return _row_to_record(row) if row is not None else None


def load_model_artifact(session_factory: sessionmaker[Session], record_id: int) -> MetaModel:
    with session_factory() as session:
        engine = session.get_bind()
        metadata.create_all(engine, checkfirst=True)
        blob = session.execute(
            sa.select(model_registry.c.artifact_blob).where(model_registry.c.id == record_id)
        ).scalar_one()
    model: MetaModel = pickle.loads(blob)  # noqa: S301 — our own trusted artifact, never external input
    return model
