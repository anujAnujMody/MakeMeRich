"""Subscribes to the OpenAlgo WS feed during market hours and appends
1-minute bars to `BarStore`.

Ticks arrive far more often than once a minute; this module aggregates raw
`market_data` (Quote-mode) frames into a running 1-minute OHLCV bar per
symbol, keyed on the tick's own minute bucket, and flushes a bar to the
`BarStore` once its minute has closed (a later tick's minute bucket moves
past it).

**Volume is a per-bar DELTA, not the tick's raw field.** OpenAlgo's WS quote
ticks report the RUNNING SESSION cumulative volume on every tick, not a
per-tick increment. Recording that field verbatim made every bar's `v`
monotonically increasing through the session, which silently defeats
`te.strategy.orb`'s volume-confirmation filter — a breakout bar late in the
session compares its cumulative-to-date against an early opening-range bar's,
so the condition passes structurally rather than because volume actually
expanded. So this module tracks the last-seen cumulative volume per symbol
across ticks and stores `last_cumulative_in_bar - cumulative_at_bar_start`
(see `_RunningBar.bar_volume`, which also handles the session rollover where
the counter restarts at ~0).

**Dedup on reconnect.** `OpenAlgoWSClient.run()` can redeliver a bar's worth
of ticks after a reconnect (broker replay / at-least-once delivery). This
recorder is idempotent per `(symbol, interval, event_ts)`: it tracks which
minute buckets it has already flushed and silently drops repeats, so a
replayed batch never creates duplicate bar rows in the store.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import pandas as pd
import structlog

from te.data.barstore import BarStore

logger = structlog.get_logger(__name__)


def _minute_bucket(ts: dt.datetime) -> dt.datetime:
    return ts.astimezone(dt.UTC).replace(second=0, microsecond=0)


@dataclass
class _RunningBar:
    """`v_start`/`v_last` are CUMULATIVE session volumes as the broker
    reports them, not bar volumes — the bar's own volume is their difference,
    computed at flush time by `bar_volume()`. See `BarRecorder`'s docstring."""

    exchange: str
    o: float
    h: float
    l: float
    c: float
    v_start: float
    v_last: float
    oi: float

    def bar_volume(self) -> float:
        """Volume traded WITHIN this bar's minute.

        Clamps to `v_last` when the cumulative counter went backwards, which
        happens at a session rollover (the broker restarts the count at ~0).
        The delta against the previous session's much larger total would
        otherwise be a large negative number; post-reset, `v_last` is itself
        the session-to-date volume, which for the session's first bar is the
        best available estimate of that bar's own volume."""
        if self.v_last < self.v_start:
            return self.v_last
        return self.v_last - self.v_start


class BarRecorder:
    """Aggregates live ticks into closed 1-minute bars and appends them to
    a `BarStore`, deduping on `(symbol, event_ts)` so a redelivered tick
    batch after a WS reconnect never produces duplicate rows."""

    def __init__(self, store: BarStore, *, interval: str = "1m", source: str = "openalgo_ws") -> None:
        self._store = store
        self._interval = interval
        self._source = source
        self._open_bars: dict[tuple[str, dt.datetime], _RunningBar] = {}
        self._flushed_keys: set[tuple[str, dt.datetime]] = set()
        #: Last cumulative session volume seen per symbol, carried across
        #: bar boundaries so a new bar knows where the previous one ended.
        self._last_cumulative_volume: dict[str, float] = {}
        #: Wall-clock time of the last tick actually processed (not the
        #: tick's own `event_ts`) — this is what a feed-health check needs:
        #: "are we CURRENTLY receiving anything", not "when did the market
        #: last move". A dead broker adapter leaves the WS thread alive and
        #: `is_running()` `True` while zero ticks arrive; this is the only
        #: signal that catches that. See `WSRecorderSupervisor.feed_is_stale`.
        self._last_tick_at: dt.datetime | None = None
        #: CONSECUTIVE `store.append()` failures since the last success —
        #: reset to 0 on every successful flush, incremented on every
        #: failed one. This is what makes a persistently failing
        #: `BarStore.append` visible: `openalgo_ws.py`'s per-tick exception
        #: handling (correctly) keeps the WS connection alive, and `_flush`
        #: (correctly) keeps retrying the queued bar, so `last_tick_at`
        #: stays fresh and `is_running()` stays `True` even while zero bars
        #: are actually landing in the store. Ticks flowing, "alive" by
        #: every existing signal, and nothing written — the exact "died
        #: quietly while looking alive" shape a background task cost a
        #: full session's bar recording to once already. A supervisor can
        #: poll this the same way it polls `last_tick_at`/`is_running()`.
        self._consecutive_flush_failures: int = 0

    @property
    def last_tick_at(self) -> dt.datetime | None:
        return self._last_tick_at

    @property
    def consecutive_flush_failures(self) -> int:
        return self._consecutive_flush_failures

    def on_tick(self, message: dict[str, Any]) -> int:
        """Feeds one WS `market_data` frame in. Returns the number of bar
        rows newly appended to the store as a side effect (0 most ticks —
        a bar is only flushed once its minute has closed)."""
        data = message.get("data", {})
        symbol = data.get("symbol")
        exchange = data.get("exchange", "NSE_INDEX")
        if not symbol:
            return 0
        self._last_tick_at = dt.datetime.now(dt.UTC)

        raw_ts = data.get("timestamp")
        if raw_ts is None:
            tick_ts = dt.datetime.now(dt.UTC)
        elif isinstance(raw_ts, int | float):
            # OpenAlgo's real WS `data.timestamp` is epoch MILLISECONDS (a
            # 13-digit int, e.g. 1785383956767) — `pd.Timestamp(raw_ts)`
            # with no `unit=` defaults to interpreting a bare number as
            # NANOSECONDS, which silently misparses every real tick to
            # ~1970-01-01. Every tick then buckets into the same handful of
            # ancient minutes forever, so `_flush` (triggered only by a tick
            # landing in a LATER bucket) never fires and no bar is ever
            # written — a silent, total data-loss bug, not caught by unit
            # tests that only ever supplied ISO-string timestamps.
            tick_ts = pd.Timestamp(raw_ts, unit="ms").to_pydatetime()
        else:
            tick_ts = pd.Timestamp(raw_ts).to_pydatetime()
        if tick_ts.tzinfo is None:
            tick_ts = tick_ts.replace(tzinfo=dt.UTC)
        bucket = _minute_bucket(tick_ts)
        ltp = float(data.get("ltp", 0.0))
        volume = float(data.get("volume", 0.0))
        oi = float(data.get("oi", 0.0))

        key = (symbol, bucket)
        appended = 0

        # Flush any open bars for this symbol whose bucket has closed
        # (a tick landing in a later minute proves the earlier one is done).
        stale_keys = [k for k in self._open_bars if k[0] == symbol and k[1] < bucket]
        for stale_key in stale_keys:
            appended += self._flush(stale_key)

        if key in self._flushed_keys:
            # Already-flushed bucket replayed (e.g. after a WS reconnect) —
            # this is the dedup guard; drop it silently.
            return appended

        running = self._open_bars.get(key)
        if running is None:
            # Baseline for the delta: where this symbol's cumulative counter
            # stood at the end of its previous bar. With no previous bar
            # (first tick ever seen for this symbol) fall back to this tick's
            # own cumulative value — the bar then measures only the increments
            # observed after the first tick, which slightly undercounts that
            # one bar but is the only honest option without a prior reading.
            baseline = self._last_cumulative_volume.get(symbol, volume)
            self._open_bars[key] = _RunningBar(
                exchange=exchange, o=ltp, h=ltp, l=ltp, c=ltp, v_start=baseline, v_last=volume, oi=oi
            )
        else:
            running.h = max(running.h, ltp)
            running.l = min(running.l, ltp)
            running.c = ltp
            running.v_last = volume
            running.oi = oi

        # Tracked only for ticks that actually contributed to an open bar —
        # a replayed, already-flushed bucket returns above, so the baseline
        # can never be dragged backwards by redelivered history.
        self._last_cumulative_volume[symbol] = volume

        return appended

    def flush_all(self) -> int:
        """Force-flushes every currently open bar (e.g. at end-of-session).
        Returns rows appended."""
        appended = 0
        for key in list(self._open_bars):
            appended += self._flush(key)
        return appended

    def _flush(self, key: tuple[str, dt.datetime]) -> int:
        """Only removes `key` from `_open_bars` (and marks it flushed) AFTER
        `store.append()` succeeds. A write failure (e.g. a transient disk
        error) leaves the accumulated bar in `_open_bars` — later ticks keep
        re-identifying it as stale and retrying the flush — instead of
        silently discarding a whole minute of data on one bad write. Before
        this, the bar was popped BEFORE the write; combined with
        `openalgo_ws.py`'s per-tick exception handling (which stops that
        write failure from also killing the whole WS connection), a single
        transient write error used to both lose the bar AND never get
        retried."""
        if key in self._flushed_keys:
            return 0
        running = self._open_bars.get(key)
        if running is None:
            return 0
        symbol, bucket = key

        now = dt.datetime.now(dt.UTC)
        row = pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "exchange": running.exchange,
                    "event_ts": bucket,
                    "interval": self._interval,
                    "o": running.o,
                    "h": running.h,
                    "l": running.l,
                    "c": running.c,
                    "v": running.bar_volume(),
                    "oi": running.oi,
                    "ingested_at": now,
                    "source": self._source,
                }
            ]
        )
        try:
            written = self._store.append(row)
        except Exception:
            self._consecutive_flush_failures += 1
            logger.error(
                "bar flush failed — bar retained for a later retry, not lost",
                symbol=symbol,
                event_ts=bucket.isoformat(),
                consecutive_flush_failures=self._consecutive_flush_failures,
            )
            raise
        self._consecutive_flush_failures = 0
        self._open_bars.pop(key, None)
        self._flushed_keys.add(key)
        logger.debug(
            "bar appended",
            symbol=symbol,
            event_ts=bucket.isoformat(),
            o=running.o,
            h=running.h,
            l=running.l,
            c=running.c,
            v=running.bar_volume(),
        )
        return written
