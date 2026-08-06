"""Persists parsed `OptionBhavRow`s for real, later use (backtesting, ML
feature engineering) — the table `te.data.ingest_log`'s `bar_ingest_log`
deliberately is NOT this (it's audit-only: source/date/row-count/status,
never the option data itself).

Found live on 2026-07-30, while starting the Phase 5 bhavcopy backfill:
`te.data.bhav_ingest.ingest()` had been fetching, parsing, and logging real
row counts every single day (the scheduled 18:30 IST `bhavcopy_ingest` job,
running since Phase 1) without ever persisting the parsed `OptionBhavRow`s
anywhere — `bar_ingest_log` recorded "success, 4068 rows" while every one of
those rows was discarded the moment the ingest function returned. A
silent-data-loss gap of the exact same shape as the recorder's epoch-ms/ns
timestamp bug found the same day, just quieter — nothing ever queries this
table, so there was no downstream symptom to notice.

Declared as a plain `sqlalchemy.Core` `Table` here (not the ORM `Base` in
`te/persistence/models.py`) for the same layer-rule reason `bar_ingest_log`/
`instruments` are: `te/data/*` may only import `te.domain` among `te.*`
packages, never `te.persistence` — see `te/data/ingest_log.py`'s module
docstring for the full explanation of this pattern. `te/persistence/models.py`
declares the matching read-side ORM mirror (`OptionBhav`).
"""

from __future__ import annotations

import sqlalchemy as sa

from te.data.bhav_types import OptionBhavRow

metadata = sa.MetaData()

option_bhav = sa.Table(
    "option_bhav",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("trade_date", sa.Date, nullable=False),
    sa.Column("symbol", sa.String(16), nullable=False),
    sa.Column("expiry", sa.Date, nullable=False),
    sa.Column("strike", sa.Float, nullable=False),
    sa.Column("option_type", sa.String(2), nullable=False),
    sa.Column("exchange", sa.String(8), nullable=False),
    sa.Column("open", sa.Float, nullable=False),
    sa.Column("high", sa.Float, nullable=False),
    sa.Column("low", sa.Float, nullable=False),
    sa.Column("close", sa.Float, nullable=False),
    sa.Column("settle_price", sa.Float, nullable=False),
    sa.Column("open_interest", sa.Integer, nullable=False),
    sa.Column("change_in_oi", sa.Integer, nullable=False),
    sa.Column("volume", sa.Integer, nullable=False),
    sa.Column("source", sa.String(32), nullable=False),
    sa.UniqueConstraint(
        "trade_date", "exchange", "symbol", "expiry", "strike", "option_type", name="uq_option_bhav_contract_day"
    ),
)


def write_option_bhav_rows(engine: sa.Engine, rows: list[OptionBhavRow]) -> int:
    """Upserts `rows`, keyed on `(trade_date, exchange, symbol, expiry,
    strike, option_type)` — delete-then-insert per row, same convention as
    `te.broker.instrument_sync.sync_instruments`. Safe to re-run: a bhavcopy
    file never changes after an exchange publishes it, but re-ingesting the
    same day must replace, not duplicate, its rows."""
    metadata.create_all(engine, checkfirst=True)
    if not rows:
        return 0

    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                sa.delete(option_bhav).where(
                    option_bhav.c.trade_date == row.trade_date,
                    option_bhav.c.exchange == row.exchange,
                    option_bhav.c.symbol == row.symbol,
                    option_bhav.c.expiry == row.expiry,
                    option_bhav.c.strike == row.strike,
                    option_bhav.c.option_type == row.option_type,
                )
            )
            conn.execute(
                option_bhav.insert().values(
                    trade_date=row.trade_date,
                    symbol=row.symbol,
                    expiry=row.expiry,
                    strike=row.strike,
                    option_type=row.option_type,
                    exchange=row.exchange,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    settle_price=row.settle_price,
                    open_interest=row.open_interest,
                    change_in_oi=row.change_in_oi,
                    volume=row.volume,
                    source=row.source,
                )
            )
    return len(rows)
