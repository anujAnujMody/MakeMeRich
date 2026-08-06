"""phase 6 ml maturity tables

Revision ID: 8f1ef55537b5
Revises: c1a6f0d92b3e
Create Date: 2026-07-29 15:11:34.887639

`ml_maturity_state` (current stage, single row), `model_promotions` (audit
trail for every stage transition — see `te/ml/gates.py`), `ml_predictions`
(every shadow-or-higher prediction ever logged, never read by
`te.engine.cycle`), and `model_registry` (trained model artifacts +
provenance — see `te/ml/registry.py`).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f1ef55537b5"
down_revision: str | None = "c1a6f0d92b3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ml_maturity_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "model_promotions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_stage", sa.String(length=16), nullable=False),
        sa.Column("to_stage", sa.String(length=16), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("criteria_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ml_predictions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cycle_id", sa.Integer(), nullable=False),
        sa.Column("instrument", sa.String(length=64), nullable=False),
        sa.Column("feature_spec_name", sa.String(length=32), nullable=False),
        sa.Column("feature_spec_version", sa.Integer(), nullable=False),
        sa.Column("p", sa.Float(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("displayed", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "model_registry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("feature_spec_name", sa.String(length=32), nullable=False),
        sa.Column("feature_spec_version", sa.Integer(), nullable=False),
        sa.Column("artifact_blob", sa.LargeBinary(), nullable=False),
        sa.Column("gbm_library", sa.String(length=16), nullable=False),
        sa.Column("calibration_method", sa.String(length=16), nullable=False),
        sa.Column("calibration_slope", sa.Float(), nullable=True),
        sa.Column("dsr", sa.Float(), nullable=False),
        sa.Column("pbo", sa.Float(), nullable=False),
        sa.Column("n_trials_at_training", sa.Integer(), nullable=False),
        sa.Column("n_labeled_samples", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("model_registry")
    op.drop_table("ml_predictions")
    op.drop_table("model_promotions")
    op.drop_table("ml_maturity_state")
