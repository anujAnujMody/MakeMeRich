"""Tests for te.data.recorder.BarRecorder — 1-minute bar aggregation from
live ticks, and dedup on reconnect (a replayed tick batch must not create
duplicate bar rows)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.data.barstore import BarStore
from te.data.recorder import BarRecorder

_WINDOW_START = dt.datetime(2026, 7, 29, 9, 0, tzinfo=dt.UTC)
_WINDOW_END = dt.datetime(2026, 7, 29, 10, 0, tzinfo=dt.UTC)


def _tick(symbol: str, ts: str, ltp: float, volume: float = 100.0) -> dict[str, object]:
    return {
        "type": "market_data",
        "mode": 2,
        "topic": f"{symbol}.NSE_INDEX",
        "data": {
            "symbol": symbol,
            "exchange": "NSE_INDEX",
            "ltp": ltp,
            "volume": volume,
            "oi": 0,
            "timestamp": ts,
        },
    }


def _tick_epoch_ms(symbol: str, ts_ms: int, ltp: float, volume: float = 100.0) -> dict[str, object]:
    """OpenAlgo's REAL WS `data.timestamp` shape — epoch milliseconds as a
    bare int (e.g. `1785383956767`), not an ISO string. Confirmed live
    against the running engine during market hours on 2026-07-30, after
    `_tick()`'s ISO-string-only fixture let a real bug (every tick silently
    misbucketed to ~1970, so no bar ever flushed all session) go undetected."""
    return {
        "type": "market_data",
        "mode": 2,
        "topic": f"{symbol}.NSE_INDEX",
        "data": {
            "symbol": symbol,
            "exchange": "NSE_INDEX",
            "ltp": ltp,
            "volume": volume,
            "oi": 0,
            "timestamp": ts_ms,
        },
    }


def test_recorder_flushes_a_bar_once_its_minute_closes(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    recorder = BarRecorder(store)

    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:20:05+00:00", 24200.0))
    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:20:30+00:00", 24210.0))
    appended = recorder.on_tick(_tick("NIFTY", "2026-07-29T09:21:05+00:00", 24215.0))

    assert appended == 1  # the 09:20 bar closes when a 09:21 tick arrives

    out = store.read(symbol="NIFTY", start=_WINDOW_START, end=_WINDOW_END, interval="1m")
    assert len(out) == 1
    row = out.iloc[0]
    assert row["o"] == 24200.0
    assert row["h"] == 24210.0
    assert row["c"] == 24210.0


def test_recorder_flushes_a_bar_with_real_epoch_millisecond_timestamps(tmp_path: Path) -> None:
    """Regression test for the live-data bug found on 2026-07-30: OpenAlgo's
    real `data.timestamp` is epoch MILLISECONDS as a bare int, which
    `pd.Timestamp(raw_ts)` with no `unit=` misparses as NANOSECONDS —
    every tick landed in a ~1970 bucket and no bar ever flushed. Three
    ticks one real minute apart, as bare epoch-ms ints, must still close a
    bar in the CORRECT (2026) minute."""
    store = BarStore(tmp_path)
    recorder = BarRecorder(store)

    base_ms = 1785383956767  # 2026-07-30T03:59:16.767Z
    recorder.on_tick(_tick_epoch_ms("NIFTY", base_ms, 24200.0))
    recorder.on_tick(_tick_epoch_ms("NIFTY", base_ms + 20_000, 24210.0))
    appended = recorder.on_tick(_tick_epoch_ms("NIFTY", base_ms + 65_000, 24215.0))

    assert appended == 1

    out = store.read(
        symbol="NIFTY",
        start=dt.datetime(2026, 7, 30, 3, 0, tzinfo=dt.UTC),
        end=dt.datetime(2026, 7, 30, 4, 0, tzinfo=dt.UTC),
        interval="1m",
    )
    assert len(out) == 1
    row = out.iloc[0]
    assert row["event_ts"].year == 2026
    assert row["o"] == 24200.0
    assert row["h"] == 24210.0


def test_recorder_dedups_on_reconnect(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    recorder = BarRecorder(store)

    batch = [
        _tick("NIFTY", "2026-07-29T09:20:05+00:00", 24200.0),
        _tick("NIFTY", "2026-07-29T09:20:30+00:00", 24210.0),
        _tick("NIFTY", "2026-07-29T09:21:05+00:00", 24215.0),
    ]
    for tick in batch:
        recorder.on_tick(tick)

    out_before = store.read(symbol="NIFTY", start=_WINDOW_START, end=_WINDOW_END, interval="1m")
    assert len(out_before) == 1

    # Simulate a WS reconnect replaying the exact same tick batch (broker
    # at-least-once redelivery).
    for tick in batch:
        recorder.on_tick(tick)

    out_after = store.read(symbol="NIFTY", start=_WINDOW_START, end=_WINDOW_END, interval="1m")
    assert len(out_after) == 1, "replayed tick batch must not create a duplicate bar row"


def test_recorder_stores_per_bar_volume_delta_not_running_session_total(tmp_path: Path) -> None:
    """OpenAlgo's WS quote ticks report the RUNNING SESSION cumulative
    volume on every tick, not a per-tick delta. Storing it verbatim made
    every bar's `v` monotonically increasing through the session, which
    silently defeats `te.strategy.orb`'s volume-confirmation filter: a
    breakout bar late in the session compares its cumulative-to-date against
    an early opening-range bar's, so the condition passes structurally
    rather than because volume actually expanded.

    A bar's stored `v` must be the volume traded WITHIN that bar's minute.
    """
    store = BarStore(tmp_path)
    recorder = BarRecorder(store)

    # Cumulative session volume, as the broker reports it, tick by tick.
    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:20:05+00:00", 24200.0, volume=1_000.0))
    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:20:30+00:00", 24210.0, volume=1_500.0))
    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:21:05+00:00", 24215.0, volume=2_200.0))
    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:21:45+00:00", 24220.0, volume=2_500.0))
    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:22:05+00:00", 24225.0, volume=2_600.0))

    out = store.read(symbol="NIFTY", start=_WINDOW_START, end=_WINDOW_END, interval="1m")
    volumes = {row["event_ts"].minute: row["v"] for _, row in out.iterrows()}

    # 09:20 is the first bar seen for this symbol, so its baseline is its own
    # first tick: 1500 - 1000 = 500.
    assert volumes[20] == 500.0
    # 09:21 carries the previous bar's last cumulative (1500) as its
    # baseline: 2500 - 1500 = 1000. NOT 2500, the running total.
    assert volumes[21] == 1_000.0

    # And the decisive property the ORB filter depends on: a later bar's
    # volume is NOT structurally larger just because the session progressed.
    assert volumes[21] < 2_200.0


def test_recorder_volume_delta_survives_a_session_rollover_without_going_negative(tmp_path: Path) -> None:
    """Cumulative volume resets to ~0 at a new session. The delta against
    the previous session's much larger total would be negative; clamp to the
    tick's own (post-reset) volume instead."""
    store = BarStore(tmp_path)
    recorder = BarRecorder(store)

    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:20:05+00:00", 24200.0, volume=900_000.0))
    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:21:05+00:00", 24210.0, volume=950_000.0))
    # New session — the broker's cumulative counter restarts.
    recorder.on_tick(_tick("NIFTY", "2026-07-30T09:15:05+00:00", 24300.0, volume=300.0))
    recorder.on_tick(_tick("NIFTY", "2026-07-30T09:15:45+00:00", 24310.0, volume=800.0))
    recorder.flush_all()

    out = store.read(
        symbol="NIFTY",
        start=dt.datetime(2026, 7, 29, 9, 0, tzinfo=dt.UTC),
        end=dt.datetime(2026, 7, 30, 10, 0, tzinfo=dt.UTC),
        interval="1m",
    )
    assert (out["v"] >= 0).all(), "a session rollover must never produce a negative bar volume"

    rollover_bar = out[out["event_ts"] == dt.datetime(2026, 7, 30, 9, 15, tzinfo=dt.UTC)]
    assert len(rollover_bar) == 1
    assert rollover_bar.iloc[0]["v"] == 800.0


def test_recorder_volume_delta_is_per_symbol(tmp_path: Path) -> None:
    """Two symbols' cumulative counters are independent — one must never
    become the other's baseline."""
    store = BarStore(tmp_path)
    recorder = BarRecorder(store)

    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:20:05+00:00", 24200.0, volume=1_000.0))
    recorder.on_tick(_tick("BANKNIFTY", "2026-07-29T09:20:06+00:00", 52000.0, volume=70_000.0))
    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:21:05+00:00", 24210.0, volume=1_400.0))
    recorder.on_tick(_tick("BANKNIFTY", "2026-07-29T09:21:06+00:00", 52100.0, volume=75_000.0))
    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:22:05+00:00", 24220.0, volume=1_900.0))
    recorder.on_tick(_tick("BANKNIFTY", "2026-07-29T09:22:06+00:00", 52200.0, volume=79_000.0))

    nifty = store.read(symbol="NIFTY", start=_WINDOW_START, end=_WINDOW_END, interval="1m")
    banknifty = store.read(symbol="BANKNIFTY", start=_WINDOW_START, end=_WINDOW_END, interval="1m")

    nifty_v = {row["event_ts"].minute: row["v"] for _, row in nifty.iterrows()}
    banknifty_v = {row["event_ts"].minute: row["v"] for _, row in banknifty.iterrows()}

    assert nifty_v[21] == 400.0  # 1400 - 1000
    assert banknifty_v[21] == 5_000.0  # 75000 - 70000


def test_recorder_flush_all_flushes_open_bars(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    recorder = BarRecorder(store)

    recorder.on_tick(_tick("NIFTY", "2026-07-29T15:29:05+00:00", 24300.0))
    appended = recorder.flush_all()

    assert appended == 1


class _FailOnceStore:
    """Wraps a real `BarStore`, raising on the first `append()` call only —
    models a transient write failure (the exact scenario `openalgo_ws.py`'s
    per-tick exception handling now stops from killing the whole
    connection)."""

    def __init__(self, real: BarStore) -> None:
        self._real = real
        self._calls = 0

    def append(self, bars):  # noqa: ANN001, ANN201
        self._calls += 1
        if self._calls == 1:
            raise OSError("simulated transient write failure")
        return self._real.append(bars)


def test_a_failed_flush_keeps_the_bar_for_a_later_retry_instead_of_losing_it(tmp_path: Path) -> None:
    """Regression: `_flush` used to pop the accumulated bar out of
    `_open_bars` BEFORE calling `store.append()` — a write failure meant
    that minute's bar was gone forever, silently, with no retry. It must
    now stay queued and actually get written once a later tick retries the
    flush."""
    real_store = BarStore(tmp_path)
    flaky_store = _FailOnceStore(real_store)
    recorder = BarRecorder(flaky_store)  # type: ignore[arg-type]

    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:20:05+00:00", 24200.0))
    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:20:30+00:00", 24210.0))

    # This tick's bucket closes the 09:20 bar — the flush attempt fails.
    with pytest.raises(OSError, match="simulated transient write failure"):
        recorder.on_tick(_tick("NIFTY", "2026-07-29T09:21:05+00:00", 24215.0))

    # Nothing was written yet — but the bar must still be queued, not lost.
    assert real_store.read(symbol="NIFTY", start=_WINDOW_START, end=_WINDOW_END, interval="1m").empty

    # A later tick retries the same flush — this time the store succeeds.
    appended = recorder.on_tick(_tick("NIFTY", "2026-07-29T09:22:05+00:00", 24220.0))
    assert appended == 1

    out = real_store.read(symbol="NIFTY", start=_WINDOW_START, end=_WINDOW_END, interval="1m")
    assert len(out) == 1
    assert out.iloc[0]["o"] == 24200.0  # the original 09:20 bar's data, not lost or corrupted


class _AlwaysFailStore:
    """Models a PERSISTENTLY failing `BarStore.append` — every call raises.
    This is the "died quietly while looking alive" shape: `openalgo_ws.py`
    correctly keeps the connection up on a per-tick exception, and `_flush`
    correctly keeps retrying the queued bar, so `last_tick_at` stays fresh
    and nothing else observable changes while zero bars ever actually land."""

    def append(self, bars):  # noqa: ANN001, ANN201
        raise OSError("simulated persistent write failure")


def test_a_persistently_failing_store_is_visible_via_consecutive_flush_failures(tmp_path: Path) -> None:
    """The detection this project was missing: ticks flowing, `last_tick_at`
    fresh, and zero bars written must be OBSERVABLE by something other than
    a stdlib logging line a supervisor cannot poll."""
    recorder = BarRecorder(_AlwaysFailStore())  # type: ignore[arg-type]
    assert recorder.consecutive_flush_failures == 0

    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:20:05+00:00", 24200.0))
    with pytest.raises(OSError, match="simulated persistent write failure"):
        recorder.on_tick(_tick("NIFTY", "2026-07-29T09:21:05+00:00", 24210.0))
    assert recorder.consecutive_flush_failures == 1
    # The feed still looks alive by every other existing signal.
    assert recorder.last_tick_at is not None

    with pytest.raises(OSError, match="simulated persistent write failure"):
        recorder.on_tick(_tick("NIFTY", "2026-07-29T09:22:05+00:00", 24220.0))
    assert recorder.consecutive_flush_failures == 2, "consecutive failures must accumulate, not reset per tick"


def test_flush_failures_reset_to_zero_after_a_later_success(tmp_path: Path) -> None:
    """A transient failure that then recovers must not keep reporting
    unhealthy forever — the counter tracks CONSECUTIVE failures since the
    last success."""
    real_store = BarStore(tmp_path)
    flaky_store = _FailOnceStore(real_store)
    recorder = BarRecorder(flaky_store)  # type: ignore[arg-type]

    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:20:05+00:00", 24200.0))
    with pytest.raises(OSError, match="simulated transient write failure"):
        recorder.on_tick(_tick("NIFTY", "2026-07-29T09:21:05+00:00", 24215.0))
    assert recorder.consecutive_flush_failures == 1

    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:22:05+00:00", 24220.0))
    assert recorder.consecutive_flush_failures == 0


def test_a_failed_flush_is_logged_at_error(tmp_path: Path) -> None:
    """Structlog, not stdlib logging — this project's `caplog` reads empty
    (see `CLAUDE.md`), so a failure logged only via `logging.exception`
    (as `openalgo_ws.py`'s per-tick handler does) is invisible to anything
    reading through the project's own pipeline."""
    from structlog.testing import capture_logs

    recorder = BarRecorder(_AlwaysFailStore())  # type: ignore[arg-type]
    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:20:05+00:00", 24200.0))

    with capture_logs() as logs:
        with pytest.raises(OSError, match="simulated persistent write failure"):
            recorder.on_tick(_tick("NIFTY", "2026-07-29T09:21:05+00:00", 24210.0))

    errors = [e for e in logs if e.get("log_level") == "error"]
    assert errors, f"no error-level event was logged; got {[e.get('event') for e in logs]}"
    assert "flush failed" in errors[0]["event"]


def test_last_tick_at_tracks_wall_clock_receipt_not_the_tick_own_timestamp(tmp_path: Path) -> None:
    """`WSRecorderSupervisor.check_feed_health` (see `te.engine.scheduler`)
    needs "are we CURRENTLY receiving anything" — a dead broker adapter
    leaves the WS thread alive with `is_running()` still `True`, and only
    this catches it. Must reflect real wall-clock receipt, not the
    (possibly old, possibly replayed) `event_ts` on the tick itself."""
    recorder = BarRecorder(BarStore(tmp_path))
    assert recorder.last_tick_at is None

    before = dt.datetime.now(dt.UTC)
    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:20:05+00:00", 24200.0))
    after = dt.datetime.now(dt.UTC)

    assert recorder.last_tick_at is not None
    assert before <= recorder.last_tick_at <= after


def test_flushed_bar_ingested_at_is_real_ingestion_time_not_the_bar_open(tmp_path: Path) -> None:
    """`bars_asof`'s `ingested_at <= as_of` half exists to stop a backfilled
    or revised bar leaking into a decision made before that data existed
    (`te/data/asof.py`). That guarantee is a no-op for the live feed unless
    `ingested_at` genuinely reflects WHEN we wrote the bar, not the bar's own
    open minute — the two are usually the same to the second in the live
    path, but must never be conflated. Mirrors
    `test_last_tick_at_tracks_wall_clock_receipt_not_the_tick_own_timestamp`."""
    store = BarStore(tmp_path)
    recorder = BarRecorder(store)

    before = dt.datetime.now(dt.UTC)
    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:20:05+00:00", 24200.0))
    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:20:30+00:00", 24210.0))
    recorder.on_tick(_tick("NIFTY", "2026-07-29T09:21:05+00:00", 24215.0))  # closes and flushes the 09:20 bar
    after = dt.datetime.now(dt.UTC)

    out = store.read(symbol="NIFTY", start=_WINDOW_START, end=_WINDOW_END, interval="1m")
    assert len(out) == 1
    row = out.iloc[0]

    event_ts = row["event_ts"].to_pydatetime()
    ingested_at = row["ingested_at"].to_pydatetime()
    assert ingested_at > event_ts, (
        "ingested_at must be real wall-clock ingestion time, well after the bar's own open minute"
    )
    assert before <= ingested_at <= after


def test_last_tick_at_ignores_a_tick_with_no_symbol(tmp_path: Path) -> None:
    """A malformed frame is dropped before it can prove the feed is alive —
    it must not falsely mark the feed as healthy."""
    recorder = BarRecorder(BarStore(tmp_path))
    recorder.on_tick({"type": "market_data", "data": {}})
    assert recorder.last_tick_at is None
