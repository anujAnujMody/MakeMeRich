"""`TradingCalendar` — parsing the broker's holiday payload, and answering
"is this a session, and what are its hours?".

Rows here are copied verbatim from OpenAlgo's real `market/holidays`
response for 2026 (fetched 2026-08-01), not invented, so a change in the
broker's payload shape shows up as a failing parse test rather than as an
engine that quietly thinks every day is a holiday.
"""

from __future__ import annotations

import datetime as dt

from te.domain.calendar import TradingCalendar, from_holiday_rows
from te.domain.clock import DEFAULT_SESSION

REPUBLIC_DAY = {
    "date": "2026-01-26",
    "description": "Republic Day",
    "holiday_type": "TRADING_HOLIDAY",
    "closed_exchanges": ["NSE", "BSE", "NFO", "BFO", "CDS", "BCD", "MCX"],
    "open_exchanges": [],
}
#: MCX stays open on this one — the reason the calendar is exchange-scoped.
MAHARASHTRA_ELECTION = {
    "date": "2026-01-15",
    "description": "Municipal Corporation Election - Maharashtra",
    "holiday_type": "TRADING_HOLIDAY",
    "closed_exchanges": ["NSE", "BSE", "NFO", "BFO", "CDS", "BCD"],
    "open_exchanges": [{"exchange": "MCX", "start_time": 1768476600000, "end_time": 1768501500000}],
}
MUHURAT = {
    "date": "2026-11-08",
    "description": "Diwali Laxmi Pujan (Muhurat Trading)",
    "holiday_type": "SPECIAL_SESSION",
    "closed_exchanges": [],
    "open_exchanges": [
        {"exchange": "NSE", "start_time": 1794141000000, "end_time": 1794145500000},
        {"exchange": "BSE", "start_time": 1794141000000, "end_time": 1794145500000},
    ],
}

CALENDAR = from_holiday_rows([REPUBLIC_DAY, MAHARASHTRA_ELECTION, MUHURAT])


def test_a_listed_holiday_is_not_a_trading_day() -> None:
    assert CALENDAR.is_trading_day(dt.date(2026, 1, 26), exchange="NSE") is False


def test_an_ordinary_weekday_is_a_trading_day() -> None:
    """Absence from a holiday list is meaningful — the list is exceptions,
    not an allowlist."""
    assert CALENDAR.is_trading_day(dt.date(2026, 1, 27), exchange="NSE") is True


def test_weekends_are_never_trading_days() -> None:
    assert CALENDAR.is_trading_day(dt.date(2026, 1, 24), exchange="NSE") is False  # Saturday
    assert CALENDAR.is_trading_day(dt.date(2026, 1, 25), exchange="NSE") is False  # Sunday


def test_an_unknown_calendar_reports_no_trading_days_at_all() -> None:
    """The fail-safe. "We never fetched a calendar" must not be silently
    equal to "there are no holidays this year" — the second reads as a
    perfectly normal year and trades straight through Republic Day."""
    unknown = TradingCalendar.unknown()
    assert unknown.is_trading_day(dt.date(2026, 1, 27), exchange="NSE") is False
    assert unknown.session_window(dt.date(2026, 1, 27), exchange="NSE") is None
    assert unknown.next_trading_day(dt.date(2026, 1, 27), exchange="NSE") is None


def test_holidays_are_per_exchange() -> None:
    """MCX trades on the Maharashtra election date; NSE does not. This
    engine only touches NSE/BSE, but conflating the two is how a calendar
    silently becomes wrong when exchanges diverge."""
    assert CALENDAR.is_trading_day(dt.date(2026, 1, 15), exchange="NSE") is False
    assert dt.date(2026, 1, 15) not in CALENDAR.holidays_for("MCX")


def test_a_regular_session_gets_the_regular_window() -> None:
    assert CALENDAR.session_window(dt.date(2026, 1, 27), exchange="NSE") == DEFAULT_SESSION


def test_a_special_session_is_a_trading_day_with_its_own_hours() -> None:
    """Muhurat trading: a real session, on a Sunday, in the evening. Every
    part of that breaks a weekday-plus-fixed-hours model."""
    on = dt.date(2026, 11, 8)
    assert on.weekday() == 6, "fixture sanity — Muhurat 2026 falls on a Sunday"
    assert CALENDAR.is_trading_day(on, exchange="NSE") is True

    window = CALENDAR.session_window(on, exchange="NSE")
    assert window is not None
    assert window.start == dt.time(18, 0)
    assert window.end == dt.time(19, 15)
    assert window != DEFAULT_SESSION


def test_next_trading_day_skips_holidays_and_weekends_together() -> None:
    """2026-01-24/25 is a weekend and the 26th is Republic Day, so the next
    session after Friday the 23rd is Tuesday the 27th. Weekday arithmetic
    alone answers Monday the 26th."""
    assert CALENDAR.next_trading_day(dt.date(2026, 1, 23), exchange="NSE") == dt.date(2026, 1, 27)


def test_a_malformed_row_is_skipped_not_fatal() -> None:
    """Losing one holiday is bad. Losing the whole year's calendar because
    one row changed shape is worse — and the second failure mode is silent,
    because an empty calendar looks exactly like a year with no holidays."""
    calendar = from_holiday_rows(
        [
            {"date": "not-a-date", "closed_exchanges": ["NSE"], "holiday_type": "TRADING_HOLIDAY"},
            {"description": "no date field at all", "closed_exchanges": ["NSE"]},
            REPUBLIC_DAY,
        ]
    )
    assert calendar.is_trading_day(dt.date(2026, 1, 26), exchange="NSE") is False
    assert calendar.known is True


def test_a_dropped_row_is_both_skipped_and_surfaced() -> None:
    """A dropped holiday row must not be silently indistinguishable from
    "not a holiday" — `known` stays True (the whole year must not be thrown
    away over one bad row) but the drop itself must be visible on the
    returned calendar, not just swallowed by a bare `continue`."""
    bad_date_row = {"date": "not-a-date", "closed_exchanges": ["NSE"], "holiday_type": "TRADING_HOLIDAY"}
    no_date_row = {"description": "no date field at all", "closed_exchanges": ["NSE"]}
    bad_closed_row = {"date": "2026-03-03", "closed_exchanges": "NSE", "holiday_type": "TRADING_HOLIDAY"}
    bad_open_row = {
        "date": "2026-03-04",
        "holiday_type": "SPECIAL_SESSION",
        "closed_exchanges": [],
        "open_exchanges": "not-a-list",
    }
    calendar = from_holiday_rows([bad_date_row, no_date_row, bad_closed_row, bad_open_row, REPUBLIC_DAY])

    assert calendar.known is True  # tolerant parsing itself is correct
    assert len(calendar.dropped_rows) == 4
    assert bad_date_row in calendar.dropped_rows
    assert no_date_row in calendar.dropped_rows
    assert bad_closed_row in calendar.dropped_rows
    assert bad_open_row in calendar.dropped_rows
    # The one well-formed row among the malformed ones must still count.
    assert calendar.is_trading_day(dt.date(2026, 1, 26), exchange="NSE") is False


def test_a_clean_calendar_reports_no_dropped_rows() -> None:
    """`dropped_rows` must not manufacture a false positive on a normal
    payload — every row here is well-formed."""
    assert CALENDAR.dropped_rows == ()


def test_a_special_session_with_unparseable_bounds_is_dropped() -> None:
    """Better to treat the date as a plain holiday than to invent a session
    window for it. Uses a WEEKDAY (2026-11-09, a Monday) rather than the real
    Muhurat date (2026-11-08, a Sunday) — on the Sunday fixture,
    `is_trading_day` reads False via the plain weekend branch regardless of
    whether the row was actually dropped, so that fixture never exercised
    this path at all. Also asserts the row itself lands on `dropped_rows`,
    matching the four other drop paths `test_a_dropped_row_is_both_skipped_and_surfaced`
    covers — a dropped row must not be silent, per the module docstring."""
    bad_row = {
        "date": "2026-11-09",
        "holiday_type": "SPECIAL_SESSION",
        "closed_exchanges": [],
        "open_exchanges": [{"exchange": "NSE", "start_time": None, "end_time": "noon"}],
    }
    calendar = from_holiday_rows([bad_row])
    assert calendar.is_trading_day(dt.date(2026, 11, 9), exchange="NSE") is True, (
        "no special session AND no plain holiday entry — an ordinary weekday reads as trading"
    )
    assert calendar.session_window(dt.date(2026, 11, 9), exchange="NSE") == DEFAULT_SESSION
    assert bad_row in calendar.dropped_rows
