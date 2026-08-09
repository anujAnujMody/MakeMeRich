"""`TradingCalendar` — is this date a session, and what are its hours?

Until now the engine answered "is the market open?" with weekday arithmetic
alone (`te.api.routers.market._next_trading_day`, and implicitly every
scheduler job whose window is a time-of-day). That is wrong on roughly 17
weekdays a year: on Republic Day the recorder subscribes to a dead feed, the
paper cycle evaluates a rule against bars that will never arrive, and the
dashboard names a holiday as the next trading day.

It is also wrong in the OTHER direction once a year. Diwali Muhurat trading
is a genuine session on a date the exchange is otherwise closed, and it runs
for about an hour in the EVENING — nothing like 09:15-15:30. A calendar that
only knows "closed" cannot express it, so this one carries a per-date
session window too.

PURE, per the layer rule: this module holds the calendar and answers
questions about it. Fetching it from the broker is `te.broker`'s job and
persisting it is `te.engine.state`'s — see `te.engine.trading_calendar` for
the wiring that joins the three.

### Why exchange-scoped

Holidays are not uniform. In the 2026 list, MCX stays open on 12 of the 17
dates NSE/BSE close, and the equity exchanges close on Maharashtra-specific
dates the rest of the country trades through. This engine only touches
NSE/BSE/NFO/BFO, but a calendar keyed by exchange costs nothing and removes
a whole class of "which exchange was that holiday for?" mistakes.

### Failing safe

`is_trading_day` returns `False` for any date the calendar does not cover
ONLY when the calendar is empty-by-construction (`TradingCalendar.unknown()`);
a loaded calendar treats an uncovered weekday as a trading day, because a
holiday list is a list of EXCEPTIONS and absence from it is meaningful. The
distinction matters: "we never fetched a calendar" and "we fetched one and
this date isn't a holiday" must not produce the same answer, or a failed
fetch silently becomes a year with no holidays.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from te.domain.clock import DEFAULT_SESSION, SessionWindow

#: The exchanges this engine trades. Used to normalise/validate the
#: per-exchange keys coming back from the broker's calendar.
TRADED_EXCHANGES = ("NSE", "BSE", "NFO", "BFO")


@dataclass(frozen=True)
class SpecialSession:
    """A date that is a holiday for normal purposes but carries a real,
    usually short, trading window — Diwali Muhurat trading being the one
    that actually occurs. `window` is in IST, like every other session
    time in this codebase."""

    date: dt.date
    window: SessionWindow
    description: str = ""


@dataclass(frozen=True)
class TradingCalendar:
    """`closed` maps exchange -> the dates it is shut. `special` maps
    exchange -> that exchange's special sessions by date.

    Construct via `from_holiday_rows` (broker payload) or `unknown` (nothing
    fetched yet). A directly-constructed empty calendar is indistinguishable
    from a fetched one that found no holidays, which is why `known` is an
    explicit field rather than inferred from emptiness."""

    known: bool
    closed: dict[str, frozenset[dt.date]] = field(default_factory=dict)
    special: dict[str, dict[dt.date, SpecialSession]] = field(default_factory=dict)
    #: Rows `from_holiday_rows` could not parse (bad `date`, non-list
    #: `closed_exchanges`/`open_exchanges`), kept verbatim so a caller that
    #: can log (this module is pure, per the layer rule — see
    #: `te.engine.trading_calendar`) can report exactly which rows were
    #: dropped rather than just a count. A dropped row is NOT the same as
    #: "not a holiday" — see the module docstring's "Failing safe" section —
    #: so this must stay visible rather than vanish into a silent `continue`.
    dropped_rows: tuple[dict[str, object], ...] = ()

    @staticmethod
    def unknown() -> TradingCalendar:
        """No calendar has been fetched. Every date reads as a non-session,
        so the engine stands down rather than trading blind on a day it
        cannot classify. Loud by construction: a stood-down engine gets
        investigated, a silently-holiday-free year does not."""
        return TradingCalendar(known=False)

    def is_trading_day(self, on: dt.date, *, exchange: str) -> bool:
        if not self.known:
            return False
        if on in self.special.get(exchange, {}):
            return True  # Muhurat: closed for settlement, open for trading
        if on.weekday() >= 5:
            return False
        return on not in self.closed.get(exchange, frozenset())

    def session_window(self, on: dt.date, *, exchange: str) -> SessionWindow | None:
        """The hours this exchange trades on `on` — `None` when it does not
        trade at all. A special session returns ITS window, not the regular
        one; using 09:15-15:30 on Muhurat day would have the engine idle
        through the entire real session and then "trade" for hours after the
        close."""
        if not self.is_trading_day(on, exchange=exchange):
            return None
        session = self.special.get(exchange, {}).get(on)
        return session.window if session is not None else DEFAULT_SESSION

    def next_trading_day(self, after: dt.date, *, exchange: str, limit: int = 30) -> dt.date | None:
        """First trading day strictly after `after`. `None` if none is found
        within `limit` days — which means the calendar has run out (it holds
        one year), not that the exchange has closed forever. Callers must
        not fall back to "the next weekday" on `None`: that is the exact
        approximation this class exists to remove."""
        for offset in range(1, limit + 1):
            candidate = after + dt.timedelta(days=offset)
            if self.is_trading_day(candidate, exchange=exchange):
                return candidate
        return None

    def holidays_for(self, exchange: str) -> frozenset[dt.date]:
        return self.closed.get(exchange, frozenset())


def from_holiday_rows(rows: list[dict[str, object]], *, ist_offset_minutes: int = 330) -> TradingCalendar:
    """Builds a calendar from OpenAlgo's `market/holidays` payload.

    Each row carries `date`, `holiday_type`, `closed_exchanges` and
    `open_exchanges` (the latter a list of `{exchange, start_time, end_time}`
    with epoch-MILLISECOND bounds). A `SPECIAL_SESSION` row has an EMPTY
    `closed_exchanges` and lists every exchange under `open_exchanges` with
    its real window — that is how Muhurat trading is expressed.

    `ist_offset_minutes` converts those epoch-ms bounds to IST wall-clock.
    It is a parameter rather than a `ZoneInfo` lookup so this module stays
    dependency-free and pure; India has a single fixed offset with no DST,
    so a constant is exact here rather than an approximation.

    Rows are tolerated, not trusted: a malformed date or a window whose
    bounds don't parse is skipped rather than aborting the whole calendar.
    Losing one holiday is bad; losing the entire year's calendar because one
    row changed shape is worse. But a dropped row must not be silent —
    `known` stays `True` (refusing the whole year over one bad row is the
    failure the tolerance exists to avoid) while the skipped rows themselves
    are carried on the returned calendar's `dropped_rows`, so a caller that
    can log (this module cannot — see the module docstring) is able to
    report exactly what was lost rather than have it read identically to
    "no holiday on that date".
    """
    closed: dict[str, set[dt.date]] = {ex: set() for ex in TRADED_EXCHANGES}
    special: dict[str, dict[dt.date, SpecialSession]] = {ex: {} for ex in TRADED_EXCHANGES}
    dropped: list[dict[str, object]] = []

    for row in rows:
        raw_date = row.get("date")
        if not isinstance(raw_date, str):
            dropped.append(row)
            continue
        try:
            on = dt.date.fromisoformat(raw_date)
        except ValueError:
            dropped.append(row)
            continue

        description = str(row.get("description", ""))
        closed_names = row.get("closed_exchanges") or []
        if not isinstance(closed_names, list):
            dropped.append(row)
            continue
        for name in closed_names:
            exchange = str(name).upper()
            if exchange in closed:
                closed[exchange].add(on)

        if row.get("holiday_type") != "SPECIAL_SESSION":
            continue
        open_entries = row.get("open_exchanges") or []
        if not isinstance(open_entries, list):
            dropped.append(row)
            continue
        for entry in open_entries:
            if not isinstance(entry, dict):
                continue
            exchange = str(entry.get("exchange", "")).upper()
            if exchange not in special:
                continue
            window = _window_from_epoch_ms(entry.get("start_time"), entry.get("end_time"), ist_offset_minutes)
            if window is not None:
                special[exchange][on] = SpecialSession(date=on, window=window, description=description)
            else:
                # A dropped row must not be silent — see the module
                # docstring and `dropped_rows`'s own docstring. Skipping the
                # session without recording it here would read identically
                # to "no special session on this date", which is the exact
                # silent failure this field exists to prevent.
                dropped.append(row)

    return TradingCalendar(
        known=True,
        closed={ex: frozenset(dates) for ex, dates in closed.items()},
        special=special,
        dropped_rows=tuple(dropped),
    )


def _window_from_epoch_ms(start: object, end: object, offset_minutes: int) -> SessionWindow | None:
    if not isinstance(start, int | float) or not isinstance(end, int | float):
        return None
    tz = dt.timezone(dt.timedelta(minutes=offset_minutes))
    try:
        start_ist = dt.datetime.fromtimestamp(float(start) / 1000, tz=tz)
        end_ist = dt.datetime.fromtimestamp(float(end) / 1000, tz=tz)
    except (OverflowError, OSError, ValueError):
        return None
    return SessionWindow(start=start_ist.time(), end=end_ist.time())
