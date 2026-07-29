"""trade review fields on trades

Revision ID: f7c3b90d4e21
Revises: e5b2c8d41a70
Create Date: 2026-07-29 19:30:00.000000

Adds the three columns a post-hoc trade review needs and `trades` did not
have:

- `stop_paise` / `target_paise` — the levels the position was OPENED with.
  These live on `open_positions`, which is closed out the moment a trade is
  recorded, so today "what were we actually risking on this trade?" is
  unanswerable from the trade record alone. Copied off the `OpenPositionRow`
  at close time by `te.persistence.repos.paper_trading.insert_trade`.
  `stop_paise` is the ORIGINAL stop, never the trailed `current_stop_paise`.
- `cycle_evaluation_id` — back-reference to the `cycle_evaluations` row whose
  firing produced the trade. Added now but NEVER POPULATED yet:
  `open_positions` carries no evaluation reference, so wiring one end-to-end
  needs a matching column there too. Landing the (nullable) column now means
  that later change is a pure backfill rather than a second migration of this
  table.

All three are nullable — existing rows genuinely do not have this data and
inventing a value for them would be a fabrication.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7c3b90d4e21"
down_revision: str | None = "e5b2c8d41a70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite cannot ALTER TABLE ADD a column carrying a FOREIGN KEY clause,
    # so the named-constraint add is done inside a batch (table-rebuild)
    # operation, which is a no-op wrapper on backends that support it
    # natively.
    with op.batch_alter_table("trades") as batch_op:
        batch_op.add_column(sa.Column("stop_paise", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("target_paise", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("cycle_evaluation_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_trades_cycle_evaluation_id_cycle_evaluations",
            "cycle_evaluations",
            ["cycle_evaluation_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("trades") as batch_op:
        batch_op.drop_constraint("fk_trades_cycle_evaluation_id_cycle_evaluations", type_="foreignkey")
        batch_op.drop_column("cycle_evaluation_id")
        batch_op.drop_column("target_paise")
        batch_op.drop_column("stop_paise")
