"""Tests for te.data.asof.bars_asof — THE point-in-time bar read.

These three tests are standing invariants (never delete per the plan's
"Verification" section): partial-bar exclusion, late-ingestion exclusion,
and the positive case proving the first two aren't vacuous.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from te.data.asof import bars_asof
from te.data.barstore import BarStore


def _bar(
    *,
    symbol: str = "NIFTY",
    event_ts: dt.datetime,
    ingested_at: dt.datetime,
    interval: str = "1m",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "exchange": "NSE_INDEX",
        "event_ts": event_ts,
        "interval": interval,
        "o": 100.0,
        "h": 101.0,
        "l": 99.0,
        "c": 100.5,
        "v": 1000.0,
        "oi": 0.0,
        "ingested_at": ingested_at,
        "source": "openalgo_ws",
    }


def test_bars_asof_excludes_partial_bar(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    open_ts = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)  # bar spans 09:20-09:21
    store.append(pd.DataFrame([_bar(event_ts=open_ts, ingested_at=open_ts)]))

    as_of = dt.datetime(2026, 7, 29, 9, 20, 30, tzinfo=dt.UTC)  # bar hasn't closed yet
    out = bars_asof(store, "NIFTY", as_of=as_of, lookback=dt.timedelta(hours=1), interval="1m")

    assert out.empty


def test_bars_asof_excludes_late_ingested(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    open_ts = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)  # closes 09:21, well before as_of
    late_ingest = dt.datetime(2026, 7, 30, 9, 0, tzinfo=dt.UTC)  # recorded a day later (backfill)
    store.append(pd.DataFrame([_bar(event_ts=open_ts, ingested_at=late_ingest)]))

    as_of = dt.datetime(2026, 7, 29, 15, 30, tzinfo=dt.UTC)
    out = bars_asof(store, "NIFTY", as_of=as_of, lookback=dt.timedelta(hours=6), interval="1m")

    assert out.empty


def test_bars_asof_includes_closed_bar_ingested_in_time(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    open_ts = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)  # closes 09:21
    ingested = open_ts + dt.timedelta(seconds=5)
    store.append(pd.DataFrame([_bar(event_ts=open_ts, ingested_at=ingested)]))

    as_of = dt.datetime(2026, 7, 29, 9, 25, tzinfo=dt.UTC)
    out = bars_asof(store, "NIFTY", as_of=as_of, lookback=dt.timedelta(hours=1), interval="1m")

    assert len(out) == 1
    assert out.iloc[0]["event_ts"] == pd.Timestamp(open_ts)


def test_bars_asof_requires_timezone_aware_as_of(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        bars_asof(store, "NIFTY", as_of=dt.datetime(2026, 7, 29, 9, 25), lookback=dt.timedelta(hours=1))


def test_interval_to_timedelta_parses_common_intervals() -> None:
    from te.data.asof import interval_to_timedelta

    assert interval_to_timedelta("1m") == dt.timedelta(minutes=1)
    assert interval_to_timedelta("5m") == dt.timedelta(minutes=5)
    assert interval_to_timedelta("1h") == dt.timedelta(hours=1)
    assert interval_to_timedelta("1d") == dt.timedelta(days=1)


def test_interval_to_timedelta_rejects_unknown_format() -> None:
    from te.data.asof import interval_to_timedelta

    with pytest.raises(ValueError, match="Unrecognised interval"):
        interval_to_timedelta("weekly")
