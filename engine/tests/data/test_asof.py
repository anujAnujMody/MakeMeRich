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

from te.data.asof import bars_asof, latest_close_paise
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


def test_bars_asof_includes_a_bar_at_the_exact_close_boundary(tmp_path: Path) -> None:
    """`as_of == close_ts` is the PRODUCTION case, not an edge case — the
    paper cycle is scheduled ON the minute boundary. `<=` (not `<`) on close
    must include a bar that closes exactly at `as_of`, or every live cycle
    would evaluate on data one bar stale."""
    store = BarStore(tmp_path)
    open_ts = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)  # closes exactly 09:21
    store.append(pd.DataFrame([_bar(event_ts=open_ts, ingested_at=open_ts)]))

    as_of = dt.datetime(2026, 7, 29, 9, 21, tzinfo=dt.UTC)  # == close_ts exactly
    out = bars_asof(store, "NIFTY", as_of=as_of, lookback=dt.timedelta(hours=1), interval="1m")

    assert len(out) == 1


def test_bars_asof_excludes_a_bar_one_microsecond_before_its_close(tmp_path: Path) -> None:
    """Control for the test above — one microsecond earlier than `close_ts`
    the bar has not closed yet and must not be visible."""
    store = BarStore(tmp_path)
    open_ts = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)  # closes exactly 09:21
    store.append(pd.DataFrame([_bar(event_ts=open_ts, ingested_at=open_ts)]))

    as_of = dt.datetime(2026, 7, 29, 9, 21, tzinfo=dt.UTC) - dt.timedelta(microseconds=1)
    out = bars_asof(store, "NIFTY", as_of=as_of, lookback=dt.timedelta(hours=1), interval="1m")

    assert out.empty


def test_bars_asof_includes_a_bar_ingested_at_the_exact_as_of_instant(tmp_path: Path) -> None:
    """`as_of == ingested_at` exactly must also be visible — the recorder's
    live `ingested_at` and the scheduler's `as_of` can plausibly land on the
    same instant, and `<=` must include it."""
    store = BarStore(tmp_path)
    open_ts = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)  # closes 09:21
    as_of = dt.datetime(2026, 7, 29, 9, 25, tzinfo=dt.UTC)
    store.append(pd.DataFrame([_bar(event_ts=open_ts, ingested_at=as_of)]))  # ingested_at == as_of exactly

    out = bars_asof(store, "NIFTY", as_of=as_of, lookback=dt.timedelta(hours=1), interval="1m")

    assert len(out) == 1


def test_bars_asof_excludes_a_bar_ingested_one_microsecond_after_as_of(tmp_path: Path) -> None:
    """Control for the test above — ingested one microsecond after `as_of`
    must not be visible."""
    store = BarStore(tmp_path)
    open_ts = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)  # closes 09:21
    as_of = dt.datetime(2026, 7, 29, 9, 25, tzinfo=dt.UTC)
    late_ingest = as_of + dt.timedelta(microseconds=1)
    store.append(pd.DataFrame([_bar(event_ts=open_ts, ingested_at=late_ingest)]))

    out = bars_asof(store, "NIFTY", as_of=as_of, lookback=dt.timedelta(hours=1), interval="1m")

    assert out.empty


def test_bars_asof_includes_the_oldest_bar_at_the_lookback_plus_interval_edge(tmp_path: Path) -> None:
    """`window_start = as_of - lookback - bar_span` — the extra `bar_span`
    of left-edge slack (per the module docstring) means a bar whose OPEN
    sits exactly `lookback + bar_span` before `as_of` is still the oldest
    bar the requested lookback should surface, and must be included. A
    `Sub` -> `Add` flip on the `- bar_span` term would push `window_start`
    two bar-spans later, silently dropping this bar (and, in production,
    starving any lookback-sized feature computation of its oldest sample)
    without any existing test noticing, since they all place bars
    comfortably inside the window rather than at this exact edge."""
    store = BarStore(tmp_path)
    as_of = dt.datetime(2026, 7, 29, 9, 25, tzinfo=dt.UTC)
    lookback = dt.timedelta(hours=1)
    # window_start = 09:25 - 1:00:00 - 0:01:00 (1m bar_span) = 08:24:00 exactly.
    open_ts = dt.datetime(2026, 7, 29, 8, 24, tzinfo=dt.UTC)
    store.append(pd.DataFrame([_bar(event_ts=open_ts, ingested_at=open_ts)]))

    out = bars_asof(store, "NIFTY", as_of=as_of, lookback=lookback, interval="1m")

    assert len(out) == 1
    assert out.iloc[0]["event_ts"] == pd.Timestamp(open_ts)


def test_latest_close_paise_converts_rupees_to_paise_by_multiplying_by_100(tmp_path: Path) -> None:
    """`latest_close_paise` converts the bar's rupee close (`c`) to integer
    paise via `* 100` — a `Mult` -> `FloorDiv` flip would instead compute
    `c // 100`, collapsing every realistic close price (tens to thousands
    of rupees) to a tiny, wrong paise value with no existing test to catch
    it (nothing in this suite calls `latest_close_paise` directly)."""
    store = BarStore(tmp_path)
    open_ts = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)  # closes 09:21
    store.append(pd.DataFrame([_bar(event_ts=open_ts, ingested_at=open_ts)]))  # c=100.5

    as_of = dt.datetime(2026, 7, 29, 9, 25, tzinfo=dt.UTC)
    result = latest_close_paise(store, "NIFTY", as_of, fallback=1)

    assert result == 10_050  # Rs 100.50 -> 10,050 paise


def test_latest_close_paise_falls_back_when_no_bar_is_visible(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    as_of = dt.datetime(2026, 7, 29, 9, 25, tzinfo=dt.UTC)

    result = latest_close_paise(store, "NIFTY", as_of, fallback=7_400)

    assert result == 7_400


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
