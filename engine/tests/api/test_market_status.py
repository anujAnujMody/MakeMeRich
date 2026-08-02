"""Regression for a real bug found live on 2026-07-31: `/api/market-status`
was a permanent Phase-0 stub — `status="closed", label="Unknown"` no matter
the real time — missed by the earlier Tier-0 dashboard-wiring pass (which
fixed dashboard/pnl/trades/positions/orders but not this router). It now
computes the real IST session state from `te.domain.clock`.

Extended 2026-08-01 with the exchange holiday calendar. Weekday arithmetic
alone was wrong in both directions: it reported a full open session on
Republic Day, and it named exchange holidays as "the next trading day".
"""

from __future__ import annotations

import datetime as dt

from te.api.routers.market import _compute_market_session
from te.domain.calendar import TradingCalendar, from_holiday_rows
from te.domain.clock import IST

#: Real rows from OpenAlgo's published 2026 calendar (`market/holidays`).
#: 2026-10-02 (Gandhi Jayanti) is a Friday; 2026-11-08 is Diwali Muhurat
#: trading — a genuine ~1-hour EVENING session on a date the exchange is
#: otherwise closed. The epoch-ms bounds are the broker's own.
_ROWS: list[dict[str, object]] = [
    {
        "date": "2026-10-02",
        "description": "Mahatma Gandhi Jayanti",
        "holiday_type": "TRADING_HOLIDAY",
        "closed_exchanges": ["NSE", "BSE", "NFO", "BFO"],
        "open_exchanges": [],
    },
    {
        "date": "2026-11-08",
        "description": "Diwali Laxmi Pujan (Muhurat Trading)",
        "holiday_type": "SPECIAL_SESSION",
        "closed_exchanges": [],
        "open_exchanges": [
            {"exchange": "NSE", "start_time": 1794141000000, "end_time": 1794145500000},
        ],
    },
]

CALENDAR = from_holiday_rows(_ROWS)


def _at(year: int, month: int, day: int, hour: int, minute: int) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=IST)


def test_reports_open_during_the_regular_session() -> None:
    # 2026-07-31 is a Friday.
    session = _compute_market_session(_at(2026, 7, 31, 10, 0), CALENDAR)
    assert session.status == "open"
    assert session.label == "Market open"
    assert session.nextEvent == "2026-07-31T15:30:00+05:30"


def test_reports_pre_open_before_915_ist_on_a_weekday() -> None:
    session = _compute_market_session(_at(2026, 7, 31, 9, 0), CALENDAR)
    assert session.status == "pre-open"
    assert session.label == "Market opens soon"
    assert session.nextEvent == "2026-07-31T09:15:00+05:30"


def test_reports_closed_after_1530_ist_on_a_weekday() -> None:
    session = _compute_market_session(_at(2026, 7, 31, 16, 0), CALENDAR)
    assert session.status == "closed"
    assert session.label == "Market closed"
    # Next trading day after Friday is Monday.
    assert session.nextEvent == "2026-08-03T09:15:00+05:30"


def test_reports_closed_on_a_weekend() -> None:
    # 2026-08-01 is a Saturday.
    session = _compute_market_session(_at(2026, 8, 1, 12, 0), CALENDAR)
    assert session.status == "closed"
    assert "weekend" in session.label.lower()
    assert session.nextEvent == "2026-08-03T09:15:00+05:30"


def test_an_exchange_holiday_is_not_an_open_session() -> None:
    """The core failure of weekday arithmetic. 2026-10-02 is a Friday, and
    10:00 on a Friday is squarely inside 09:15-15:30 — so the old version
    reported "Market open" on Gandhi Jayanti."""
    session = _compute_market_session(_at(2026, 10, 2, 10, 0), CALENDAR)
    assert session.status == "closed"
    assert "holiday" in session.label.lower()


def test_the_next_trading_day_skips_a_holiday() -> None:
    """2026-10-02 is a Friday holiday, so the next session is Monday the
    5th. The weekday-only version returned Saturday the 3rd."""
    session = _compute_market_session(_at(2026, 10, 1, 16, 0), CALENDAR)
    assert session.nextEvent == "2026-10-05T09:15:00+05:30"


def test_muhurat_trading_is_a_real_session_with_its_own_hours() -> None:
    """Diwali Muhurat trading is the case a closed/open calendar alone
    cannot express: a genuine session, on a date the exchange is otherwise
    shut, running in the EVENING. Using 09:15-15:30 here would idle through
    all of it and then report "open" for hours after it ended."""
    # 2026-11-08 is a Sunday — a weekday check would rule it out twice over.
    during = _compute_market_session(_at(2026, 11, 8, 18, 45), CALENDAR)
    assert during.status == "open"

    before = _compute_market_session(_at(2026, 11, 8, 10, 0), CALENDAR)
    assert before.status == "pre-open"

    after = _compute_market_session(_at(2026, 11, 8, 20, 0), CALENDAR)
    assert after.status == "closed"


def test_an_unfetched_calendar_reports_unavailable_not_open() -> None:
    """`TradingCalendar.unknown()` must never read as "closed for the
    weekend" or, worse, "open". Not knowing what day it is is its own
    state, and the label says so."""
    session = _compute_market_session(_at(2026, 7, 31, 10, 0), TradingCalendar.unknown())
    assert session.status == "closed"
    assert "unavailable" in session.label.lower()
    assert session.nextEvent == "", "must not guess a next session it cannot compute"
