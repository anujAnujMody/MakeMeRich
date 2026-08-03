"""Characterization tests for the NSE/BSE bhavcopy ingest wrappers — the
try/fetch/parse/record_ingest/re-raise shape both exchanges share.

Success writes one `bar_ingest_log` row with `status="success"` and the
parsed row count; ANY fetch/parse failure writes `status="failed"` with
`row_count=0` and the exception text as `detail`, then re-raises (the
error must never be swallowed — a silently-empty ingest is exactly the
bug class that leaves the model training on a hole in the history).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import sqlalchemy as sa

import te.data.bhavcopy_bse as bse
import te.data.bhavcopy_nse as nse
from te.data.bhav_store import option_bhav
from te.data.ingest_log import bar_ingest_log

TRADE_DATE = dt.date(2026, 7, 29)


@pytest.fixture
def engine(tmp_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path / 'ingest.db'}", future=True)


def _log_rows(engine: sa.Engine) -> list[sa.Row]:  # type: ignore[type-arg]
    with engine.begin() as conn:
        return list(conn.execute(sa.select(bar_ingest_log)).all())


@pytest.mark.parametrize(
    ("module", "fixture_name", "ingest", "source"),
    [
        (nse, "bhavcopy_nse_fo_golden.csv", "ingest_bhavcopy_nse", "nse_bhavcopy"),
        (bse, "bhavcopy_bse_fo_golden.csv", "ingest_bhavcopy_bse", "bse_bhavcopy"),
    ],
)
def test_ingest_logs_success_with_the_parsed_row_count(
    engine: sa.Engine,
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    fixture_name: str,
    ingest: str,
    source: str,
) -> None:
    csv_text = (Path(__file__).parent.parent / "fixtures" / fixture_name).read_text()
    fetch_name = ingest.replace("ingest_", "fetch_")
    monkeypatch.setattr(module, fetch_name, lambda trade_date, **kw: csv_text)

    rows = getattr(module, ingest)(engine, TRADE_DATE)
    assert rows, "golden fixture must parse to at least one option row"

    logged = _log_rows(engine)
    assert len(logged) == 1
    assert logged[0].source == source
    assert logged[0].trade_date == TRADE_DATE
    assert logged[0].status == "success"
    assert logged[0].row_count == len(rows)

    # Regression, found live on 2026-07-30: `bar_ingest_log` recorded a real
    # row count every day while the parsed rows themselves were silently
    # discarded — nothing ever persisted the actual option data. The count
    # matching alone (asserted above) is not enough to catch that; this
    # proves the rows are genuinely queryable afterward, not just counted.
    with engine.begin() as conn:
        persisted = list(conn.execute(sa.select(option_bhav)).all())
    assert len(persisted) == len(rows)
    assert {p.trade_date for p in persisted} == {TRADE_DATE}
    assert {p.source for p in persisted} == {source}


@pytest.mark.parametrize(
    ("module", "ingest", "source"),
    [
        (nse, "ingest_bhavcopy_nse", "nse_bhavcopy"),
        (bse, "ingest_bhavcopy_bse", "bse_bhavcopy"),
    ],
)
def test_ingest_logs_failed_and_reraises_on_fetch_error(
    engine: sa.Engine,
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    ingest: str,
    source: str,
) -> None:
    def _boom(trade_date: dt.date, **kw: object) -> str:
        raise RuntimeError("archive returned 403")

    monkeypatch.setattr(module, ingest.replace("ingest_", "fetch_"), _boom)

    with pytest.raises(RuntimeError, match="archive returned 403"):
        getattr(module, ingest)(engine, TRADE_DATE)

    logged = _log_rows(engine)
    assert len(logged) == 1
    assert logged[0].source == source
    assert logged[0].status == "failed"
    assert logged[0].row_count == 0
    assert "archive returned 403" in logged[0].detail


def test_nse_and_bse_send_identical_browser_shaped_headers() -> None:
    """NSE's edge 403s a bare `httpx.get`; both archives are fetched with
    the same browser-shaped headers, from one shared constant."""
    assert nse._HEADERS is bse._HEADERS
    assert "User-Agent" in nse._HEADERS
