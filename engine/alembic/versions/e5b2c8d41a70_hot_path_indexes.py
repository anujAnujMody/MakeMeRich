"""hot-path indexes

Revision ID: e5b2c8d41a70
Revises: d3a7c9e1f2b4
Create Date: 2026-07-29 18:00:00.000000

Indexes for the four queries that run on a per-cycle (or per-evaluation)
cadence and were doing full table scans:

- `trades.closed_at` — every daily-limit check (`te.risk.limits`) and the
  Tier-3 rolling window (`te.risk.monitors.RollingPerformance`) filters on
  this column, once per instrument per cycle.
- `open_positions.closed_at` — `closed_at IS NULL` is what "currently open"
  means; read every entry AND exit cycle.
- `slippage_observations (instrument, id)` — Tier-0's rolling window filters
  by instrument and orders by `id DESC`; the composite serves both.
- `evaluation_conditions.evaluation_id` — this is the one that made
  `te.ml.labeling.label_firings_from_evaluations` quadratic: one lookup per
  evaluation against an unindexed column over the whole firing history.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b2c8d41a70"
down_revision: str | None = "d3a7c9e1f2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_trades_closed_at", "trades", ["closed_at"])
    op.create_index("ix_open_positions_closed_at", "open_positions", ["closed_at"])
    op.create_index("ix_slippage_observations_instrument_id", "slippage_observations", ["instrument", "id"])
    op.create_index("ix_evaluation_conditions_evaluation_id", "evaluation_conditions", ["evaluation_id"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_conditions_evaluation_id", table_name="evaluation_conditions")
    op.drop_index("ix_slippage_observations_instrument_id", table_name="slippage_observations")
    op.drop_index("ix_open_positions_closed_at", table_name="open_positions")
    op.drop_index("ix_trades_closed_at", table_name="trades")
