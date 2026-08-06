"""`TrialLedger` — every trial (backtest run, CV inner loop, grid-search
point, hyperparameter sweep, ...) ever run, permanently. This is the
monotonic input `te.ml.metrics.deflated_sharpe_ratio()`'s `n_trials` and
`mean_sharpe`/`var_sharpe` come from — resetting the trial count to make DSR
look better is EXACTLY the prior failure mode this table exists to prevent
(the old engine's fabricated in-sample metrics).

**There is deliberately no `delete()` method anywhere in this class, no repo
function, no route that can remove a row.** The only way to honestly improve
DSR is to actually find a real edge, never to shrink N. A SQLite trigger
(`trg_trial_ledger_no_delete`, created both here and in the Alembic
migration `alembic/versions/`) blocks `DELETE FROM trial_ledger` at the
database layer too, so even a hand-run SQL console command is rejected —
belt and suspenders on top of the missing-method guarantee.

Declared with a private `sqlalchemy.Core` `Table` (matching
`te/data/ingest_log.py`'s / `te/broker/instrument_sync.py`'s pattern) so
`te/ml/*` stays inside the plan's layer rule ("risk/strategy/ml import
domain + data" — `te.ml` may not import `te.persistence`) — the caller
supplies a plain `sqlalchemy.Engine`, not a `te.persistence` session.
`te/persistence/models.py` declares the matching ORM model
(`TrialLedgerRow`) for read-side use by later phases' repos/dashboard
endpoints.
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa

from te.sqltypes import UtcDateTime

metadata = sa.MetaData()

trial_ledger = sa.Table(
    "trial_ledger",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("ts", UtcDateTime, nullable=False),
    sa.Column("kind", sa.String(32), nullable=False),  # the `scope` n_trials() counts against
    sa.Column("config_hash", sa.String(64), nullable=False),
    sa.Column("sharpe", sa.Float, nullable=False),
    sa.Column("run_id", sa.String(64), nullable=False),
)

_CREATE_NO_DELETE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_trial_ledger_no_delete
BEFORE DELETE ON trial_ledger
BEGIN
    SELECT RAISE(ABORT, 'trial_ledger is monotonic (te/ml/trials.py) — rows may never be deleted, not even by hand');
END;
"""


class TrialLedger:
    """Every trial ever run. `record()` appends; `n_trials()` counts. No
    other write operation exists."""

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine
        metadata.create_all(engine, checkfirst=True)
        if engine.dialect.name == "sqlite":
            with engine.begin() as conn:
                conn.execute(sa.text(_CREATE_NO_DELETE_TRIGGER))

    def record(self, *, kind: str, config_hash: str, sharpe: float, run_id: str) -> int:
        """Appends one trial row. Returns its `id`. There is no
        corresponding way to remove it."""
        with self._engine.begin() as conn:
            result = conn.execute(
                trial_ledger.insert().values(
                    ts=dt.datetime.now(dt.UTC),
                    kind=kind,
                    config_hash=config_hash,
                    sharpe=sharpe,
                    run_id=run_id,
                )
            )
            inserted_id = result.inserted_primary_key
        assert inserted_id is not None
        return int(inserted_id[0])

    def n_trials(self, scope: str) -> int:
        """Count of every trial ever recorded under `scope` (matched
        against `kind`) — the honest N that feeds
        `te.ml.metrics.deflated_sharpe_ratio()`."""
        with self._engine.connect() as conn:
            result = conn.execute(
                sa.select(sa.func.count()).select_from(trial_ledger).where(trial_ledger.c.kind == scope)
            )
            return int(result.scalar_one())

    def trial_sharpes(self, scope: str) -> list[float]:
        """Every recorded Sharpe ratio under `scope`, in insertion order —
        the raw input `mean`/`var` are computed from at the call site (kept
        here rather than inside `te.ml.metrics` so that module stays free
        of any DB dependency)."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                sa.select(trial_ledger.c.sharpe).where(trial_ledger.c.kind == scope).order_by(trial_ledger.c.id)
            )
            return [float(r[0]) for r in rows]
