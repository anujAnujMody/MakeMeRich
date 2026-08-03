"""Tests for te.data.ingest_log.record_ingest — bar_ingest_log writes."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import create_engine, text

from te.data.ingest_log import bar_ingest_log, record_ingest


def test_record_ingest_writes_a_row() -> None:
    engine = create_engine("sqlite:///:memory:")
    record_ingest(
        engine,
        source="nse_bhavcopy",
        trade_date=dt.date(2026, 7, 29),
        row_count=3,
        status="success",
    )

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT source, trade_date, row_count, status FROM bar_ingest_log")).fetchall()

    assert len(rows) == 1
    assert rows[0].source == "nse_bhavcopy"
    assert rows[0].row_count == 3
    assert rows[0].status == "success"
    engine.dispose()


def test_record_ingest_via_table_object_matches_migration_columns() -> None:
    assert {c.name for c in bar_ingest_log.columns} == {
        "id",
        "source",
        "trade_date",
        "ingested_at",
        "row_count",
        "status",
        "detail",
    }
