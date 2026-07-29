"""IST session clock — pure logic only, no I/O.

`SessionWindow` is a plain value the caller supplies (or defaults to
`DEFAULT_SESSION`); actual configuration of the live trading window lives in
settings/config outside `te.domain` (NSE has extended sessions before and is
subject to further change — this module never hardcodes a window into a
function that can't be overridden).

`closed_bars_asof()` is the pure comparison half of the bar-close gate;
`te/data/asof.py`'s `bars_asof()` performs the same `close_ts <= as_of`
comparison over a whole DataFrame — see that module's docstring for why it
is not refactored to delegate here.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class SessionWindow:
    start: dt.time
    end: dt.time


# NSE/BSE cash+F&O regular session, 09:15-15:30 IST as of this writing.
# Configurable, not a literal baked into call sites — pass a different
# `SessionWindow` wherever the session differs (e.g. a future NSE change).
DEFAULT_SESSION = SessionWindow(start=dt.time(9, 15), end=dt.time(15, 30))


def closed_bars_asof(as_of: dt.datetime, event_ts: dt.datetime, interval: dt.timedelta) -> bool:
    """Has the bar starting at `event_ts` with the given `interval` closed by
    `as_of`? `close_ts = event_ts + interval`; the bar is visible only once
    `close_ts <= as_of` — this is the pure arithmetic half of the lookahead
    defence `te/data/asof.py::bars_asof()` applies over a whole DataFrame."""
    if as_of.tzinfo is None or event_ts.tzinfo is None:
        raise ValueError("as_of and event_ts must be timezone-aware")
    close_ts = event_ts + interval
    return close_ts <= as_of


def to_utc(ts: dt.datetime, *, name: str = "timestamp") -> dt.datetime:
    """Normalise a tz-aware timestamp to UTC, rejecting naive input.

    SQLite's `DateTime(timezone=True)` round-trips whatever wall-clock digits
    it was given WITHOUT converting to a common offset, and comes back
    tz-NAIVE on read (a real sqlite3/SQLAlchemy limitation, not a choice) —
    so every timestamp written to the DB is normalised here first, and
    `assume_utc()` reattaches `dt.UTC` on read. Mixing tz-aware inputs
    without this would silently corrupt duration comparisons (`now -
    opened_at`, TTL checks, ...) across a DB round trip.

    `name` only customises the error message, for call sites validating a
    specific named field."""
    if ts.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware, got naive {ts!r}")
    return ts.astimezone(dt.UTC)


def assume_utc(ts: dt.datetime) -> dt.datetime:
    """The read-side counterpart to `to_utc()`: a naive value read back out
    of SQLite is known to already hold UTC wall-clock digits (every write
    went through `to_utc()`), so reattach the tzinfo rather than convert.
    Already-aware values pass through unchanged."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=dt.UTC)


def is_market_open(moment: dt.datetime, session: SessionWindow = DEFAULT_SESSION) -> bool:
    """Whether `moment`'s local time-of-day falls within `session`. Callers
    are responsible for passing `moment` already converted to the relevant
    exchange's local time (typically `IST`)."""
    local_time = moment.timetz().replace(tzinfo=None)
    return session.start <= local_time <= session.end
