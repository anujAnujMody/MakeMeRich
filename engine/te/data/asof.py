"""`bars_asof()` — THE point-in-time bar read.

This is the single most important correctness primitive in the whole data
layer. Every other module that needs bars (strategy, ml, backtest, engine)
MUST go through this function; nothing else in the codebase may read bars
directly from `BarStore`.

The filter is `close_ts <= as_of AND ingested_at <= as_of`, where
`close_ts = event_ts + interval`:

- The `<=` on **close** (not open) is the entire lookahead defence — a bar
  that opened before `as_of` but hasn't closed yet must not be visible,
  because its OHLC isn't final.
- The `ingested_at <= as_of` filter is what kills backfill/revision leakage
  — a row whose `event_ts` is safely in the past but that we only recorded
  (e.g. via a late bhavcopy backfill) after `as_of` must not be visible
  either, because at `as_of` we did not yet know it.
"""

from __future__ import annotations

import datetime as dt
import re

import pandas as pd

from te.data.barstore import BarStore

_INTERVAL_RE = re.compile(r"^(\d+)([smhd])$")
_UNIT_TO_KWARG = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def interval_to_timedelta(interval: str) -> dt.timedelta:
    """Parses a bar interval string (`"1m"`, `"5m"`, `"1h"`, `"1d"`) into a
    `timedelta`. Raises `ValueError` on anything else."""
    match = _INTERVAL_RE.match(interval)
    if match is None:
        raise ValueError(f"Unrecognised interval format: {interval!r} (expected e.g. '1m', '5m', '1h', '1d')")
    quantity, unit = match.groups()
    return dt.timedelta(**{_UNIT_TO_KWARG[unit]: int(quantity)})


def bars_asof(
    store: BarStore,
    symbol: str,
    as_of: dt.datetime,
    lookback: dt.timedelta,
    interval: str = "1m",
) -> pd.DataFrame:
    """Returns only bars whose `close_ts <= as_of AND ingested_at <= as_of`.

    `close_ts = event_ts + interval`. Bars covering `[as_of - lookback -
    interval, as_of]` are candidates; the extra `interval` of slack on the
    left edge ensures the oldest bar in the requested lookback window is
    still included even though its own open sits slightly before the
    window start.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")

    bar_span = interval_to_timedelta(interval)
    window_start = as_of - lookback - bar_span

    df = store.read(symbol=symbol, start=window_start, end=as_of, interval=interval, ingested_before=as_of)
    if df.empty:
        return df

    as_of_ts = pd.Timestamp(as_of)
    close_ts = df["event_ts"] + bar_span
    mask = (close_ts <= as_of_ts) & (df["ingested_at"] <= as_of_ts)
    return df.loc[mask].sort_values("event_ts").reset_index(drop=True)


def latest_close_paise(
    store: BarStore,
    symbol: str,
    as_of: dt.datetime,
    *,
    fallback: int,
    lookback: dt.timedelta = dt.timedelta(minutes=5),
) -> int:
    """Latest closed-bar price for `symbol` as of `as_of`, in paise, via
    `bars_asof` (never a direct `BarStore.read`). Falls back to `fallback`
    paise — typically a position's own entry premium — when no bar is
    visible yet (e.g. immediately after entry, before the WS recorder has
    appended a fresh bar): a neutral, non-crashing mark, never an invented
    number. The one shared mark-price lookup for the paper-cycle exit path,
    manual square-off, and any mark-to-market display — so they can never
    quietly disagree on what "current price" means."""
    df = bars_asof(store, symbol, as_of, lookback=lookback)
    if df.empty:
        return fallback
    return int(round(float(df.iloc[-1]["c"]) * 100))
