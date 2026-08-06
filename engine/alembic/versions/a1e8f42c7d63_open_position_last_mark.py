"""last observed mark on open_positions

Revision ID: a1e8f42c7d63
Revises: b7d4e6f19a2c
Create Date: 2026-07-31 13:20:00.000000

Adds `last_mark_paise` / `last_mark_at` to `open_positions`.

An option contract is chosen dynamically per signal, so it is never in the
WS recorder's subscription list (only the four index spot symbols are) and
therefore has NO recorded bars. Its only live mark is a per-cycle REST
quote. When that quote failed, `_current_premium_from_quotes` fell back to
bars — always empty for an option — and then to the position's own ENTRY
premium, which reads as "price unchanged":

- no stop, target or trailing exit could fire, because current == entry;
- `unrealized_pnl_paise` returned ~0 for every open position, so the
  daily-loss halt and the drawdown breaker were blind to open losses;
- and any exit taken in that state recorded a fabricated exit price and a
  gross P&L of exactly zero straight into `trades`.

Persisting the last SUCCESSFUL observation replaces that fabricated
fallback with a real, if stale, price, and `last_mark_at` makes the
staleness measurable rather than invisible.

Both nullable: a position between opening and its first successful mark
genuinely has no observation, and inventing one is the exact failure this
migration exists to end.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1e8f42c7d63"
down_revision: str | None = "b7d4e6f19a2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("open_positions") as batch_op:
        batch_op.add_column(sa.Column("last_mark_paise", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("last_mark_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("open_positions") as batch_op:
        batch_op.drop_column("last_mark_at")
        batch_op.drop_column("last_mark_paise")
