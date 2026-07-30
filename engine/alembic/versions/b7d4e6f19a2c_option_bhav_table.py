"""option_bhav table

Revision ID: b7d4e6f19a2c
Revises: f7c3b90d4e21
Create Date: 2026-07-30 07:50:00.000000

The actual historical bhavcopy DATA table — found missing live on
2026-07-30 while starting the Phase 5 backfill: `bar_ingest_log` (added in
`029448a5af9b`) only ever recorded audit metadata (source/date/row-count/
status); the parsed `OptionBhavRow`s themselves were fetched, parsed, and
discarded every single day since Phase 1, with no downstream consumer to
notice. See `te/data/bhav_store.py`'s module docstring.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d4e6f19a2c"
down_revision: str | None = "f7c3b90d4e21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "option_bhav",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("option_type", sa.String(length=2), nullable=False),
        sa.Column("exchange", sa.String(length=8), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("settle_price", sa.Float(), nullable=False),
        sa.Column("open_interest", sa.Integer(), nullable=False),
        sa.Column("change_in_oi", sa.Integer(), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trade_date", "exchange", "symbol", "expiry", "strike", "option_type", name="uq_option_bhav_contract_day"
        ),
    )


def downgrade() -> None:
    op.drop_table("option_bhav")
