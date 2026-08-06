"""`bar_ingest_log` writer — records what was ingested, when, row counts,
and source, for every bhavcopy ingest run.

Declared with a private `sqlalchemy.Core` `Table` (not the ORM models in
`te/persistence/models.py`) so `te/data/*` stays inside the plan's layer
rule ("data/broker subpackages may only import domain" among `te.*`
packages) while still being able to write its own audit row — the caller
supplies a plain `sqlalchemy.Engine`/`Connection`, not a `te.persistence`
session. `te/persistence/models.py` declares the matching ORM model
(`BarIngestLog`) for read-side use by later phases/repos; the physical
table is the single source of truth (see `alembic/versions/` for the
migration that creates it) — reconcile the two declarations onto one when
`te/persistence/repos/` is built out.
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa

metadata = sa.MetaData()

bar_ingest_log = sa.Table(
    "bar_ingest_log",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("source", sa.String(32), nullable=False),  # "nse_bhavcopy" | "bse_bhavcopy"
    sa.Column("trade_date", sa.Date, nullable=False),
    sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("row_count", sa.Integer, nullable=False),
    sa.Column("status", sa.String(16), nullable=False),  # "success" | "partial" | "failed"
    sa.Column("detail", sa.Text, nullable=False, default=""),
)


def already_ingested(engine: sa.Engine, *, source: str, trade_date: dt.date) -> bool:
    """True when this source/date pair already has a SUCCESSFUL ingest row.

    Exists so the bhavcopy job can be scheduled several times an evening
    and stay a no-op after the first one that works. NSE publishes its F&O
    bhavcopy around 20:00 IST, but the job used to fire once at 18:30 and
    give up: on 2026-08-03 that produced a 404 for a file that downloaded
    fine an hour later, and NIFTY/BANKNIFTY premiums for the day were
    simply missing until a human noticed. The same 404 appears elsewhere in
    the log's history, so the single early attempt had been quietly losing
    days for some time.

    Deliberately keyed on `status == "success"` rather than mere presence:
    the failed 18:30 attempt writes a row too, and treating that as "done"
    would reproduce exactly the bug this prevents.
    """
    metadata.create_all(engine, checkfirst=True)
    with engine.connect() as conn:
        found = conn.execute(
            sa.select(bar_ingest_log.c.id)
            .where(
                bar_ingest_log.c.source == source,
                bar_ingest_log.c.trade_date == trade_date,
                bar_ingest_log.c.status == "success",
            )
            .limit(1)
        ).first()
    return found is not None


def record_ingest(
    engine: sa.Engine,
    *,
    source: str,
    trade_date: dt.date,
    row_count: int,
    status: str,
    detail: str = "",
) -> None:
    """Writes one `bar_ingest_log` row. Idempotently creates the table if
    it doesn't exist yet (defensive for scripts/tests run ahead of Alembic;
    in normal operation the table already exists via migration)."""
    metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(
            bar_ingest_log.insert().values(
                source=source,
                trade_date=trade_date,
                ingested_at=dt.datetime.now(dt.UTC),
                row_count=row_count,
                status=status,
                detail=detail,
            )
        )
