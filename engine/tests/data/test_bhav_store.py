"""Tests for te.data.bhav_store.write_option_bhav_rows — the actual
historical-data persistence `te.data.bhav_ingest.ingest()` was missing until
2026-07-30 (see that module's docstring: `bar_ingest_log` recorded real row
counts every day while the parsed rows themselves were silently discarded)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import sqlalchemy as sa

from te.data.bhav_store import option_bhav, write_option_bhav_rows
from te.data.bhav_types import OptionBhavRow


@pytest.fixture
def engine(tmp_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path / 'bhav_store.db'}", future=True)


def _row(**overrides: object) -> OptionBhavRow:
    defaults: dict[str, object] = {
        "trade_date": dt.date(2026, 7, 29),
        "symbol": "NIFTY",
        "expiry": dt.date(2026, 8, 4),
        "strike": 24500.0,
        "option_type": "CE",
        "exchange": "NFO",
        "open": 100.0,
        "high": 120.0,
        "low": 95.0,
        "close": 110.0,
        "settle_price": 108.0,
        "open_interest": 50_000,
        "change_in_oi": 1_000,
        "volume": 200_000,
        "source": "nse_bhavcopy",
    }
    defaults.update(overrides)
    return OptionBhavRow(**defaults)  # type: ignore[arg-type]


def _select_all(engine: sa.Engine) -> list[sa.Row]:  # type: ignore[type-arg]
    with engine.begin() as conn:
        return list(conn.execute(sa.select(option_bhav)).all())


def test_write_persists_rows_queryable_afterward(engine: sa.Engine) -> None:
    written = write_option_bhav_rows(engine, [_row()])
    assert written == 1

    rows = _select_all(engine)
    assert len(rows) == 1
    assert rows[0].symbol == "NIFTY"
    assert rows[0].strike == 24500.0
    assert rows[0].close == 110.0
    assert rows[0].open_interest == 50_000


def test_write_is_empty_safe(engine: sa.Engine) -> None:
    assert write_option_bhav_rows(engine, []) == 0
    assert _select_all(engine) == []


def test_rewriting_the_same_contract_day_replaces_not_duplicates(engine: sa.Engine) -> None:
    """Re-running an ingest for a day already stored (e.g. re-running the
    backfill script) must replace that row, not accumulate a duplicate —
    same idempotency convention as `te.broker.instrument_sync.sync_instruments`."""
    write_option_bhav_rows(engine, [_row(close=110.0)])
    write_option_bhav_rows(engine, [_row(close=115.0)])

    rows = _select_all(engine)
    assert len(rows) == 1
    assert rows[0].close == 115.0


def test_different_contracts_on_the_same_day_coexist(engine: sa.Engine) -> None:
    write_option_bhav_rows(
        engine,
        [
            _row(strike=24500.0, option_type="CE"),
            _row(strike=24500.0, option_type="PE"),
            _row(strike=24600.0, option_type="CE"),
            _row(symbol="BANKNIFTY", strike=52000.0, option_type="CE"),
        ],
    )
    assert len(_select_all(engine)) == 4


def test_same_contract_different_trade_dates_both_persist(engine: sa.Engine) -> None:
    write_option_bhav_rows(engine, [_row(trade_date=dt.date(2026, 7, 28))])
    write_option_bhav_rows(engine, [_row(trade_date=dt.date(2026, 7, 29))])
    assert len(_select_all(engine)) == 2
