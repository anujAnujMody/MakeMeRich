"""profit lock fields on open_positions

Revision ID: d8a3e5c1f947
Revises: c4f81a9e6b52
Create Date: 2026-08-04 08:20:00.000000

Adds `profit_lock_activation_paise` / `profit_lock_buffer_pct` /
`profit_lock_engaged` to `open_positions`.

The ONE-TIME profit lock: once a position's profit reaches a configured
threshold, its stop jumps exactly once to a percentage below the price AT
THAT MOMENT (not below entry — a real profit floor), then freezes. Backtested
2026-08-04 against 1,305 real historical NIFTY ORB trades before being
enabled live: roughly flat mean R (-0.104 vs -0.102 baseline) but a
materially higher win rate (50.7% vs 46.2%) and no more full round-trips back
to the original stop on a trade that was already deep in profit. A
deliberate risk-shaping choice, not a claimed profitability improvement --
see `te/domain/geometry.py`'s `ExitLevels.profit_lock_activation` docstring.

`profit_lock_activation_paise`/`profit_lock_buffer_pct` mirror the static
plan (both NULL together when the rule is off, same convention as
`trailing_distance_paise`). `profit_lock_engaged` is live position state --
False until the lock fires, then permanently True for that position's life,
regardless of what the stop does afterward.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8a3e5c1f947"
down_revision: str | None = "c4f81a9e6b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("open_positions") as batch_op:
        batch_op.add_column(sa.Column("profit_lock_activation_paise", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("profit_lock_buffer_pct", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("profit_lock_engaged", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("open_positions") as batch_op:
        batch_op.drop_column("profit_lock_engaged")
        batch_op.drop_column("profit_lock_buffer_pct")
        batch_op.drop_column("profit_lock_activation_paise")
