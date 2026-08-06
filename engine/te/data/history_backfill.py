"""Backfills historical 1-minute INDEX bars from OpenAlgo's `/api/v1/history`
into the `BarStore`.

Why this exists: the WS recorder only started running in July 2026, so the
store holds a few weeks of bars. Replaying the ORB rule over real history —
the only way to reach a usable ML sample count this decade, see
`te.backtest.replay` — needs the months before that.

**Only indices.** Verified against the live OpenAlgo instance on 2026-07-31:
`history` on an EXPIRED option symbol returns
`Symbol 'NIFTY30SEP2524500CE' not found for exchange 'NFO'`. An option
contract's candles disappear with the contract, which is exactly why the
bhavcopy ingest (`te.data.bhavcopy_nse`, `option_bhav`) exists. Index spot
symbols have no expiry and go back years.

### The `ingested_at` decision

`bars_asof()` — the whole lookahead defence — admits a bar only when
`close_ts <= as_of AND ingested_at <= as_of`. Stamping a backfill with
`ingested_at = now()` would make every historical bar invisible to any
replay whose `as_of` is in the past, and the replay would silently produce
zero signals.

So backfilled bars are stamped `ingested_at = close_ts`: "recorded the
instant it closed". That is a deliberate, bounded claim, and it is honest
here for one specific reason — **exchange 1-minute index bars are not
revised**. The value fetched today for 09:16 on 2025-09-02 is the same value
that was observable at 09:17 on 2025-09-02. Nothing is being back-dated into
existence that a live recorder would not have had.

It would NOT be honest for a revised series (bhavcopy settlement prices,
restated fundamentals), and this function must never be pointed at one. The
`source` column records `openalgo_history` rather than `openalgo_ws` so a
backfilled bar is always distinguishable from a live-recorded one after the
fact.

### Known data limitation, deliberately not papered over

Index bars come back with `volume = 0` — both from this endpoint and from
the live WS feed (verified on stored parquet, 2026-07-31). An index has no
traded volume of its own; only its derivatives do. `te.strategy.orb`'s
volume-confirmation condition therefore compares `0 >= 0` and always passes,
live and in replay alike. That is pre-existing behaviour and is left exactly
as-is here: this module records what the exchange reports and does not
invent a volume proxy to make a strategy condition look meaningful.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd
import structlog

from te.broker.openalgo_rest import OpenAlgoRestClient, OpenAlgoRestError
from te.data.barstore import BAR_COLUMNS, BarStore

logger = structlog.get_logger(__name__)

#: Matches the `interval` written into the store and the partition path.
INTERVAL = "1m"

#: Broker interval code -> the `interval` value written into the store.
#:
#: OpenAlgo names the daily bar `D`; `te.ml.dataset` reads daily history as
#: `1d` (`bars_asof(..., interval="1d")`), and `BarStore` partitions on that
#: literal. They must be translated, not assumed equal — a daily backfill
#: stored under `interval=D` is invisible to every reader in this codebase,
#: which is exactly how four of the eight model features stayed NaN after a
#: successful-looking 78,000-bar backfill.
STORE_INTERVAL: dict[str, str] = {"1m": "1m", "D": "1d"}

#: How long after `event_ts` a bar of each interval is actually KNOWN.
#:
#: For a daily bar this is deliberately a full day: the close of day D is not
#: observable until D has ended, so a feature computed intraday on day D must
#: see only through D-1. Stamping `ingested_at` a day forward makes
#: `bars_asof` enforce that rather than relying on callers to remember it.
_KNOWN_AFTER: dict[str, dt.timedelta] = {"1m": dt.timedelta(minutes=1), "D": dt.timedelta(days=1)}

#: `source` marker distinguishing a backfilled bar from a live-recorded one
#: (`openalgo_ws`). Never collapse these into one value — provenance of a
#: bar is exactly the sort of thing that becomes unanswerable later.
SOURCE = "openalgo_history"


@dataclass(frozen=True)
class BackfillResult:
    symbol: str
    exchange: str
    requested_from: dt.date
    requested_to: dt.date
    bars_written: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _parse_ts(raw: str) -> dt.datetime | None:
    """Parse a history bar timestamp, in either shape OpenAlgo emits.

    The REST endpoint returns **epoch seconds as a string** (`"1780285500"`),
    verified against the live instance on 2026-07-31. Some clients and the
    MCP wrapper hand back ISO-8601 (`"2025-09-02T09:54:00.000Z"`) for the
    same data, so both are accepted rather than assuming whichever one was
    seen first.

    This distinction is not cosmetic: an epoch string silently failing an
    ISO-only parse is how a 7,500-bar fetch turns into 0 rows written, and
    an epoch string mis-parsed as 0 is how a `month=1970-01` partition
    appears in the store. A malformed timestamp drops its row rather than
    crashing a 200-day backfill, but is never coerced to 0.
    """
    if not raw:
        return None
    if raw.isdigit():
        seconds = int(raw)
        if seconds <= 0:
            return None
        return dt.datetime.fromtimestamp(seconds, tz=dt.UTC)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.UTC)


def backfill_index_bars(
    client: OpenAlgoRestClient,
    store: BarStore,
    *,
    symbol: str,
    exchange: str,
    start: dt.date,
    end: dt.date,
    interval: str = INTERVAL,
) -> BackfillResult:
    """Fetches `[start, end]` 1-minute bars for one index and appends them.

    Returns a `BackfillResult` rather than raising: a multi-symbol,
    multi-month backfill must be able to report a partial failure and carry
    on, not lose the work already done to one bad window.
    """
    store_interval = STORE_INTERVAL.get(interval, interval)
    known_after = _KNOWN_AFTER.get(interval, dt.timedelta(minutes=1))
    try:
        raw = client.history(symbol, exchange, interval, start.isoformat(), end.isoformat())
    except OpenAlgoRestError as exc:
        logger.warning("history fetch failed", symbol=symbol, start=str(start), end=str(end), error=str(exc))
        return BackfillResult(symbol, exchange, start, end, 0, error=str(exc))

    rows: list[dict[str, object]] = []
    for bar in raw:
        event_ts = _parse_ts(bar.timestamp)
        if event_ts is None:
            continue
        rows.append(
            {
                "symbol": symbol,
                "exchange": exchange,
                "event_ts": event_ts,
                "interval": store_interval,
                "o": bar.open,
                "h": bar.high,
                "l": bar.low,
                "c": bar.close,
                "v": bar.volume,
                "oi": bar.oi,
                # See the module docstring: "recorded the instant it closed".
                "ingested_at": event_ts + known_after,
                "source": SOURCE,
            }
        )

    if not rows:
        return BackfillResult(symbol, exchange, start, end, 0)

    frame = pd.DataFrame(rows, columns=list(BAR_COLUMNS))
    written = store.append(frame)
    logger.info(
        "backfilled index bars", symbol=symbol, interval=store_interval,
        start=str(start), end=str(end), rows=written,
    )
    return BackfillResult(symbol, exchange, start, end, written)


def month_windows(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    """Splits `[start, end]` into calendar-month windows.

    The history endpoint is fetched a month at a time rather than in one
    200-day request: a single failure then costs one month of the backfill
    instead of all of it, and the response stays a size the broker will
    actually return."""
    windows: list[tuple[dt.date, dt.date]] = []
    cursor = start.replace(day=1)
    while cursor <= end:
        next_month = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        window_start = max(cursor, start)
        window_end = min(next_month - dt.timedelta(days=1), end)
        if window_start <= window_end:
            windows.append((window_start, window_end))
        cursor = next_month
    return windows
