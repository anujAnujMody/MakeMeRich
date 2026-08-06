"""phase 7 monitor tables

Revision ID: d3a7c9e1f2b4
Revises: 8f1ef55537b5
Create Date: 2026-07-29 16:00:00.000000

`slippage_observations` (Tier 0 — see `te/risk/monitors.py::SlippageMonitor`),
`monitor_state` (Tier 1 CUSUM S+/S- state, persisted so a restart can't
silently reset an in-progress breach — see `CusumMonitor`), and
`backtest_drawdown_envelopes` (Tier 2 — the backtest's own bootstrapped
drawdown percentiles, computed once and read-only thereafter by
`DrawdownEnvelope`).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3a7c9e1f2b4"
down_revision: str | None = "8f1ef55537b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "slippage_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("instrument", sa.String(length=64), nullable=False),
        sa.Column("expected_paise", sa.Integer(), nullable=False),
        sa.Column("actual_paise", sa.Integer(), nullable=False),
        sa.Column("diff_paise", sa.Integer(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "monitor_state",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("s_pos", sa.Float(), nullable=False),
        sa.Column("s_neg", sa.Float(), nullable=False),
        sa.Column("last_action", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "backtest_drawdown_envelopes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("percentile", sa.Float(), nullable=False),
        sa.Column("drawdown_paise", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "percentile", name="uq_backtest_drawdown_envelopes_run_percentile"),
    )


def downgrade() -> None:
    op.drop_table("backtest_drawdown_envelopes")
    op.drop_table("monitor_state")
    op.drop_table("slippage_observations")
