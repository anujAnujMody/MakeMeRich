"""Tests for te.data.barstore.BarStore — Parquet-backed, partitioned by
interval/symbol/month; event_ts (bar open) + ingested_at (when we recorded
it) are both required columns."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from te.data.barstore import BAR_COLUMNS, BarStore


def _bar(
    *,
    symbol: str = "NIFTY",
    exchange: str = "NSE_INDEX",
    event_ts: dt.datetime,
    interval: str = "1m",
    o: float = 100.0,
    h: float = 101.0,
    l: float = 99.0,
    c: float = 100.5,
    v: float = 1000.0,
    oi: float = 0.0,
    ingested_at: dt.datetime | None = None,
    source: str = "openalgo_ws",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "exchange": exchange,
        "event_ts": event_ts,
        "interval": interval,
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "v": v,
        "oi": oi,
        "ingested_at": ingested_at or (event_ts + dt.timedelta(seconds=5)),
        "source": source,
    }


def test_barstore_append_and_read_roundtrip(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    base = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)
    bars = pd.DataFrame(
        [
            _bar(event_ts=base),
            _bar(event_ts=base + dt.timedelta(minutes=1)),
            _bar(event_ts=base + dt.timedelta(minutes=2)),
        ]
    )

    written = store.append(bars)
    assert written == 3

    out = store.read(
        symbol="NIFTY",
        start=base - dt.timedelta(minutes=5),
        end=base + dt.timedelta(minutes=5),
        interval="1m",
    )

    assert len(out) == 3
    assert list(out["c"]) == [100.5, 100.5, 100.5]
    assert out["event_ts"].is_monotonic_increasing


def test_barstore_read_filters_by_symbol_and_window(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    base = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)
    bars = pd.DataFrame(
        [
            _bar(symbol="NIFTY", event_ts=base),
            _bar(symbol="BANKNIFTY", event_ts=base),
            _bar(symbol="NIFTY", event_ts=base + dt.timedelta(days=40)),
        ]
    )
    store.append(bars)

    out = store.read(
        symbol="NIFTY",
        start=base - dt.timedelta(minutes=1),
        end=base + dt.timedelta(minutes=1),
        interval="1m",
    )
    assert len(out) == 1
    assert out.iloc[0]["symbol"] == "NIFTY"


def test_barstore_read_filters_by_ingested_before(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    base = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)
    late_ingest = base + dt.timedelta(days=1)
    bars = pd.DataFrame(
        [
            _bar(event_ts=base, ingested_at=base + dt.timedelta(seconds=5)),
            _bar(event_ts=base, ingested_at=late_ingest),
        ]
    )
    store.append(bars)

    out = store.read(
        symbol="NIFTY",
        start=base - dt.timedelta(minutes=1),
        end=base + dt.timedelta(minutes=1),
        interval="1m",
        ingested_before=base + dt.timedelta(minutes=1),
    )
    assert len(out) == 1


def test_barstore_partition_layout_on_disk(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    event_ts = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)
    store.append(pd.DataFrame([_bar(event_ts=event_ts)]))

    expected_dir = tmp_path / "interval=1m" / "symbol=NIFTY" / "month=2026-07"
    assert expected_dir.is_dir()
    assert list(expected_dir.glob("part-*.parquet"))


def test_barstore_append_rejects_missing_columns(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    bad = pd.DataFrame([{"symbol": "NIFTY"}])
    with pytest.raises(ValueError, match="missing required columns"):
        store.append(bad)


def test_barstore_read_empty_when_no_data(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    out = store.read(
        symbol="NIFTY",
        start=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        end=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
        interval="1m",
    )
    assert out.empty


# ---------------------------------------------------------------------------
# Partition pruning — a narrow read must not open a whole symbol's history
# ---------------------------------------------------------------------------


def _multi_month_store(root: Path) -> BarStore:
    """One part file per month across a full year, 3 bars each."""
    store = BarStore(root)
    for month in range(1, 13):
        rows = [
            _bar(event_ts=dt.datetime(2025, month, day, 9, 15, tzinfo=dt.UTC))
            for day in (1, 15, 28)
        ]
        store.append(pd.DataFrame(rows))
    return store


def _spy_on_read_parquet(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Records the path of every parquet file physically opened."""
    import te.data.barstore as barstore_module

    opened: list[Path] = []
    real = barstore_module.pd.read_parquet

    def _spy(path, *args, **kwargs):  # noqa: ANN001, ANN202
        opened.append(Path(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(barstore_module.pd, "read_parquet", _spy)
    return opened


def test_read_only_opens_partitions_overlapping_the_requested_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`read()` is called every cycle per instrument via `bars_asof`; it must
    prune to the `month=` partitions overlapping `[start, end]` rather than
    reading (and concatenating) the symbol's entire recorded history and
    filtering afterwards."""
    store = _multi_month_store(tmp_path / "bars")
    opened = _spy_on_read_parquet(monkeypatch)

    out = store.read(
        symbol="NIFTY",
        start=dt.datetime(2025, 6, 10, tzinfo=dt.UTC),
        end=dt.datetime(2025, 6, 20, tzinfo=dt.UTC),
        interval="1m",
    )

    months_opened = {p.parent.name for p in opened}
    assert months_opened == {"month=2025-06"}, f"opened partitions outside the range: {sorted(months_opened)}"

    # ...and the result is still exactly right.
    assert list(out["event_ts"]) == [pd.Timestamp("2025-06-15 09:15", tz="UTC")]


def test_read_spanning_a_month_boundary_opens_both_months_and_no_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _multi_month_store(tmp_path / "bars")
    opened = _spy_on_read_parquet(monkeypatch)

    out = store.read(
        symbol="NIFTY",
        start=dt.datetime(2025, 6, 20, tzinfo=dt.UTC),
        end=dt.datetime(2025, 7, 10, tzinfo=dt.UTC),
        interval="1m",
    )

    assert {p.parent.name for p in opened} == {"month=2025-06", "month=2025-07"}
    assert list(out["event_ts"]) == [
        pd.Timestamp("2025-06-28 09:15", tz="UTC"),
        pd.Timestamp("2025-07-01 09:15", tz="UTC"),
    ]


def test_pruning_uses_utc_months_so_a_local_time_range_is_not_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`month=` is derived from the UTC `event_ts` at write time, so the
    pruning comparison must be made in UTC too — an IST-expressed range
    straddling the UTC month boundary must still open the right partitions."""
    from te.domain.clock import IST

    store = _multi_month_store(tmp_path / "bars")
    opened = _spy_on_read_parquet(monkeypatch)

    # 2025-07-01 04:00 IST == 2025-06-30 22:30 UTC -> must include June.
    out = store.read(
        symbol="NIFTY",
        start=dt.datetime(2025, 7, 1, 4, 0, tzinfo=IST),
        end=dt.datetime(2025, 7, 2, 4, 0, tzinfo=IST),
        interval="1m",
    )
    assert "month=2025-06" in {p.parent.name for p in opened}
    assert list(out["event_ts"]) == [pd.Timestamp("2025-07-01 09:15", tz="UTC")]


def test_rapid_successive_appends_never_overwrite_each_others_part_files(tmp_path: Path) -> None:
    """Part-file names used to be `part-{now():...%f}-{group_index}.parquet`.
    `datetime.now()` is NOT microsecond-precise on every platform — on
    Windows its resolution is ~15ms, so a few thousand consecutive calls can
    return only a handful of distinct values. Two `append()` calls landing in
    the same tick therefore produced the SAME filename and the second
    `pq.write_table` silently overwrote the first — losing a whole bar with
    no error anywhere.

    This is exactly the recorder's access pattern: `te.data.recorder`
    flushes one `append()` per closed bar, and a tick batch that closes two
    bars back-to-back (routine after a WS reconnect replay) hits it.
    """
    store = BarStore(tmp_path / "bars")

    # Append one bar at a time, as the recorder does, with no delay between.
    for minute in range(10):
        store.append(
            pd.DataFrame(
                [_bar(event_ts=dt.datetime(2026, 7, 29, 9, minute, tzinfo=dt.UTC))],
                columns=list(BAR_COLUMNS),
            )
        )

    out = store.read(
        symbol="NIFTY",
        start=dt.datetime(2026, 7, 29, tzinfo=dt.UTC),
        end=dt.datetime(2026, 7, 30, tzinfo=dt.UTC),
        interval="1m",
    )
    assert len(out) == 10, f"appended 10 bars but only {len(out)} survived — part files overwrote each other"
    assert [ts.minute for ts in out["event_ts"]] == list(range(10))


def test_append_never_leaves_a_partial_parquet_file_on_a_mid_write_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression, found live on 2026-07-30: `pq.write_table` used to write
    directly to the final `part-*.parquet` path. A process killed mid-write
    (a container recreate's SIGKILL after the grace period — the WS
    recorder's real access pattern) could leave a 0-byte/truncated file at
    that path, which `te.backtest.engine.run_backtest`'s first-ever run
    against real recorded bars then crashed on (`pyarrow.lib.ArrowInvalid:
    Parquet file size is 0 bytes`) — a corrupt fragment sitting in the
    normal read path with no way to distinguish it from real data short of
    a crash. Fixed via write-to-temp-then-`os.replace()`, which is atomic:
    simulating a crash during the write must leave NO `.parquet` file
    behind at all (a `.parquet.tmp` orphan is fine — `read()`'s glob only
    matches `part-*.parquet`)."""
    import pyarrow.parquet as pq

    store = BarStore(tmp_path / "bars")

    def _crash(*args: object, **kwargs: object) -> None:
        raise OSError("simulated kill mid-write")

    monkeypatch.setattr(pq, "write_table", _crash)

    with pytest.raises(OSError, match="simulated kill mid-write"):
        store.append(
            pd.DataFrame(
                [_bar(event_ts=dt.datetime(2026, 7, 29, 9, 0, tzinfo=dt.UTC))],
                columns=list(BAR_COLUMNS),
            )
        )

    part_files = list((tmp_path / "bars").rglob("part-*.parquet"))
    assert part_files == [], f"a partial .parquet file survived the simulated crash: {part_files}"


def test_read_returns_empty_without_opening_anything_when_range_predates_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _multi_month_store(tmp_path / "bars")
    opened = _spy_on_read_parquet(monkeypatch)

    out = store.read(
        symbol="NIFTY",
        start=dt.datetime(2024, 1, 1, tzinfo=dt.UTC),
        end=dt.datetime(2024, 2, 1, tzinfo=dt.UTC),
        interval="1m",
    )
    assert opened == []
    assert out.empty
    assert list(out.columns) == list(BAR_COLUMNS)
