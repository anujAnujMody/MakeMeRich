"""phase 5: trial_ledger table (monotonic, no-delete-trigger)

Revision ID: c1a6f0d92b3e
Revises: a9e115d85546
Create Date: 2026-07-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1a6f0d92b3e"
down_revision: str | None = "a9e115d85546"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREATE_NO_DELETE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_trial_ledger_no_delete
BEFORE DELETE ON trial_ledger
BEGIN
    SELECT RAISE(ABORT, 'trial_ledger is monotonic (te/ml/trials.py) — rows may never be deleted, not even by hand');
END;
"""

_DROP_NO_DELETE_TRIGGER = "DROP TRIGGER IF EXISTS trg_trial_ledger_no_delete;"


def upgrade() -> None:
    # `trial_ledger` is append-only by design — see `te/ml/trials.py`'s
    # module docstring. NO delete() method exists anywhere in the codebase
    # for this table; the trigger below is belt-and-suspenders protection
    # at the database layer, so even a hand-run `DELETE FROM trial_ledger`
    # from a DB console is rejected. Resetting the trial count to make DSR
    # look better is literally the failure mode this table exists to
    # prevent (the prior engine's fabricated in-sample metrics).
    op.create_table(
        "trial_ledger",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("sharpe", sa.Float(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(_CREATE_NO_DELETE_TRIGGER)


def downgrade() -> None:
    op.execute(_DROP_NO_DELETE_TRIGGER)
    op.drop_table("trial_ledger")
