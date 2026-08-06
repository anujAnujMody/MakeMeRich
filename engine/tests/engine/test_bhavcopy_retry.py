"""A bhavcopy that is not published yet must be retried, not written off.

Found on 2026-08-03. The job fired once at 18:30 IST and asked NSE for that
day's F&O bhavcopy; NSE publishes it around 20:00, so the download 404'd and
the job gave up. NIFTY and BANKNIFTY option premiums for the whole day were
missing — every strategy in this engine is scored on premiums — and the only
trace was one log line among thousands. Re-running the same fetch by hand at
19:35 returned 3,978 rows, so nothing was wrong except the clock. The same
404 appears elsewhere in the ingest log's history, which means this had been
quietly costing days for some time.

BSE published on time that same evening, which is why the skip is per
source: the exchange that already succeeded must not be re-fetched on every
later attempt while waiting for the one that hasn't.
"""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa

from te.data.ingest_log import already_ingested, record_ingest

_DAY = dt.date(2026, 8, 3)


@pytest.fixture
def engine() -> sa.Engine:
    return sa.create_engine("sqlite:///:memory:")


def test_nothing_ingested_yet_is_not_already_ingested(engine: sa.Engine) -> None:
    assert already_ingested(engine, source="nse_bhavcopy", trade_date=_DAY) is False


def test_a_success_marks_the_day_done(engine: sa.Engine) -> None:
    record_ingest(engine, source="nse_bhavcopy", trade_date=_DAY, row_count=3978, status="success")
    assert already_ingested(engine, source="nse_bhavcopy", trade_date=_DAY) is True


def test_a_failure_does_not_mark_the_day_done(engine: sa.Engine) -> None:
    """The exact 2026-08-03 shape: the 18:30 attempt writes a `failed` row.
    Treating any row as "done" would make the retry a no-op and reproduce
    the bug this exists to prevent."""
    record_ingest(
        engine,
        source="nse_bhavcopy",
        trade_date=_DAY,
        row_count=0,
        status="failed",
        detail="Client error '404 Not Found'",
    )
    assert already_ingested(engine, source="nse_bhavcopy", trade_date=_DAY) is False


def test_one_exchange_succeeding_does_not_cover_for_the_other(engine: sa.Engine) -> None:
    """BSE published at 18:30 while NSE had not. A shared flag would have
    skipped NSE's retry on the strength of BSE's success."""
    record_ingest(engine, source="bse_bhavcopy", trade_date=_DAY, row_count=667, status="success")
    assert already_ingested(engine, source="bse_bhavcopy", trade_date=_DAY) is True
    assert already_ingested(engine, source="nse_bhavcopy", trade_date=_DAY) is False


def test_yesterdays_success_does_not_cover_today(engine: sa.Engine) -> None:
    record_ingest(engine, source="nse_bhavcopy", trade_date=_DAY, row_count=3978, status="success")
    assert already_ingested(engine, source="nse_bhavcopy", trade_date=_DAY + dt.timedelta(days=1)) is False


def test_the_job_retries_only_what_has_not_landed(engine: sa.Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end through the scheduler's job: BSE already in, NSE not, so a
    later run in the evening must call NSE's fetch and leave BSE alone."""
    from te.engine import scheduler as scheduler_module

    record_ingest(engine, source="bse_bhavcopy", trade_date=_DAY, row_count=667, status="success")
    called: list[str] = []

    def _fake_nse(_engine: sa.Engine, trade_date: dt.date) -> list[object]:
        called.append("NSE")
        record_ingest(_engine, source="nse_bhavcopy", trade_date=trade_date, row_count=3978, status="success")
        return []

    def _fake_bse(_engine: sa.Engine, trade_date: dt.date) -> list[object]:
        called.append("BSE")
        return []

    monkeypatch.setattr(scheduler_module, "ingest_bhavcopy_nse", _fake_nse)
    monkeypatch.setattr(scheduler_module, "ingest_bhavcopy_bse", _fake_bse)

    scheduler_module._run_bhavcopy_ingest(engine, _DAY)
    assert called == ["NSE"], "the already-ingested exchange must not be re-fetched"

    # The next hourly attempt has nothing left to do.
    called.clear()
    scheduler_module._run_bhavcopy_ingest(engine, _DAY)
    assert called == [], "a fully-ingested day must cost nothing on later runs"


def test_a_failing_fetch_leaves_the_day_open_for_the_next_attempt(
    engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 must not be terminal, and must not take the other exchange down
    with it."""
    from te.engine import scheduler as scheduler_module

    def _boom(_engine: sa.Engine, trade_date: dt.date) -> list[object]:
        record_ingest(_engine, source="nse_bhavcopy", trade_date=trade_date, row_count=0, status="failed", detail="404")
        raise RuntimeError("Client error '404 Not Found'")

    def _ok_bse(_engine: sa.Engine, trade_date: dt.date) -> list[object]:
        record_ingest(_engine, source="bse_bhavcopy", trade_date=trade_date, row_count=667, status="success")
        return []

    monkeypatch.setattr(scheduler_module, "ingest_bhavcopy_nse", _boom)
    monkeypatch.setattr(scheduler_module, "ingest_bhavcopy_bse", _ok_bse)

    scheduler_module._run_bhavcopy_ingest(engine, _DAY)

    assert already_ingested(engine, source="bse_bhavcopy", trade_date=_DAY) is True
    assert (
        already_ingested(engine, source="nse_bhavcopy", trade_date=_DAY) is False
    ), "a failed fetch must leave the day retryable"
