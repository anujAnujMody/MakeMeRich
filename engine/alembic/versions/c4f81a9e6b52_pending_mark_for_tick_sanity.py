"""pending mark on open_positions, for the tick-sanity guard

Revision ID: c4f81a9e6b52
Revises: a1e8f42c7d63
Create Date: 2026-08-04 05:45:00.000000

Adds `pending_mark_paise` to `open_positions`.

Found live on 2026-08-04: the WS feed printed a single-tick premium spike on
NIFTY04AUG2624600PE (a documented class of bug on Angel's SmartAPI websocket
-- users have reported wrong high/low ticks on the broker's own forum) while
the underlying itself moved a few points. `run_exit_cycle` trusted that
number immediately, closed two open positions at a fabricated "target hit",
and booked a manufactured Rs 16,768 net profit that no real market order
could have filled at.

`last_mark_paise` (see `a1e8f42c7d63`) already holds the last CONFIRMED
price, but confirming a big jump needs to remember the CANDIDATE that
triggered the suspicion, so the NEXT cycle's quote can be compared against
it -- two consecutive readings agreeing is what turns an outlier into a real,
fast market move. `last_mark_paise` cannot double as that memory: overwriting
it with an unconfirmed candidate is exactly the bug this migration exists to
prevent. NULL means "no candidate awaiting confirmation" -- the normal case
every cycle.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f81a9e6b52"
down_revision: str | None = "a1e8f42c7d63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("open_positions") as batch_op:
        batch_op.add_column(sa.Column("pending_mark_paise", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("open_positions") as batch_op:
        batch_op.drop_column("pending_mark_paise")
