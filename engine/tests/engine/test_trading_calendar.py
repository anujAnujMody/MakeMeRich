"""Persisting the exchange calendar, and the gate it puts in front of the
paper cycle.

Before this, every exchange holiday ran a full day of one-minute cycles
against a feed that would never produce a bar, logging ordinary-looking
skips the whole time — indistinguishable in the decision log from a normal
day on which the rule simply never fired.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.domain.calendar import TradingCalendar, from_holiday_rows
from te.domain.clock import IST
from te.engine.trading_calendar import _merge, get_calendar, refresh_calendar, set_calendar, stored_years
from te.persistence.db import make_engine, make_session_factory, session_scope
from te.persistence.models import Base

ROWS: list[dict[str, object]] = [
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
        "open_exchanges": [{"exchange": "NSE", "start_time": 1794141000000, "end_time": 1794145500000}],
    },
]


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'calendar.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_nothing_stored_reads_as_unknown_not_as_no_holidays(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        assert get_calendar(session).known is False


def test_the_calendar_round_trips_including_special_sessions(session_factory) -> None:
    """The special-session window is the part most likely to be lost in
    serialisation, and the part whose loss is silent — the date would still
    read as a trading day, just with the wrong nine-to-three hours."""
    with session_scope(session_factory) as session:
        set_calendar(session, from_holiday_rows(ROWS), years=[2026])

    with session_factory() as session:
        got = get_calendar(session)

    assert got.known is True
    assert got.is_trading_day(dt.date(2026, 10, 2), exchange="NSE") is False
    window = got.session_window(dt.date(2026, 11, 8), exchange="NSE")
    assert window is not None
    assert (window.start, window.end) == (dt.time(18, 0), dt.time(19, 15))


def test_a_corrupt_stored_calendar_stands_down_rather_than_trading_blind(session_factory) -> None:
    from te.engine.state import upsert_engine_state

    with session_scope(session_factory) as session:
        upsert_engine_state(session, "trading_calendar_json", "{not json at all")

    with session_factory() as session:
        assert get_calendar(session).known is False


def test_a_failed_refresh_keeps_the_previous_calendar(session_factory) -> None:
    """A stale holiday list is wrong on at most a few dates. No holiday list
    is wrong on all of them — and stands the engine down completely."""
    with session_scope(session_factory) as session:
        set_calendar(session, from_holiday_rows(ROWS), years=[2026])

    class _Broken:
        def holiday_rows(self, year: int) -> list[dict[str, object]]:
            raise RuntimeError("broker unreachable")

    assert refresh_calendar(session_factory, _Broken()) is False  # type: ignore[arg-type]

    with session_factory() as session:
        assert get_calendar(session).is_trading_day(dt.date(2026, 10, 2), exchange="NSE") is False


def test_refreshing_a_second_year_keeps_the_first(session_factory) -> None:
    """A running engine crossing 1 January must be able to answer questions
    either side of the boundary — December's holidays do not stop mattering
    the moment January's arrive."""

    class _Client:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def holiday_rows(self, year: int) -> list[dict[str, object]]:
            self.calls.append(year)
            if year == 2026:
                return ROWS
            return [
                {
                    "date": "2027-01-26",
                    "description": "Republic Day",
                    "holiday_type": "TRADING_HOLIDAY",
                    "closed_exchanges": ["NSE"],
                    "open_exchanges": [],
                }
            ]

    client = _Client()
    refresh_calendar(session_factory, client, year=2026)  # type: ignore[arg-type]
    refresh_calendar(session_factory, client, year=2027)  # type: ignore[arg-type]

    with session_factory() as session:
        got = get_calendar(session)
        assert stored_years(session) == {2026, 2027}

    assert got.is_trading_day(dt.date(2026, 10, 2), exchange="NSE") is False
    assert got.is_trading_day(dt.date(2027, 1, 26), exchange="NSE") is False


def test_the_paper_cycle_stands_down_on_an_exchange_holiday(tmp_path: Path) -> None:
    """2026-10-02 is a Friday, and 10:00 is squarely inside the session
    window — so nothing but the calendar can stop this cycle."""
    from tests.engine.test_scheduler import _runner

    runner = _runner(tmp_path, clock=lambda: dt.datetime(2026, 10, 2, 10, 0, tzinfo=IST))
    with session_scope(runner.session_factory) as session:
        set_calendar(session, from_holiday_rows(ROWS), years=[2026])

    runner.run_once()

    assert runner.status.last_result == "skipped_not_a_trading_day"


def test_the_paper_cycle_stands_down_when_no_calendar_has_been_fetched(tmp_path: Path) -> None:
    """Distinct from the holiday result, and deliberately so: one is a
    normal expected day off, the other means the engine cannot tell what day
    it is and needs a human. Collapsing them into one status would hide the
    second inside a year of the first."""
    from te.engine.state import upsert_engine_state
    from tests.engine.test_scheduler import _runner

    runner = _runner(tmp_path, clock=lambda: dt.datetime(2026, 10, 1, 10, 0, tzinfo=IST))
    with session_scope(runner.session_factory) as session:
        upsert_engine_state(session, "trading_calendar_json", "")

    runner.run_once()

    assert runner.status.last_result == "skipped_calendar_unknown"


def test_the_paper_cycle_runs_during_a_muhurat_evening_session(tmp_path: Path) -> None:
    """The opposite failure. 2026-11-08 is a SUNDAY and 18:45 is three hours
    after the normal close — a weekday-plus-fixed-hours engine would sit out
    a real trading session entirely."""
    from tests.engine.test_scheduler import _runner

    runner = _runner(tmp_path, clock=lambda: dt.datetime(2026, 11, 8, 18, 45, tzinfo=IST))
    with session_scope(runner.session_factory) as session:
        set_calendar(session, from_holiday_rows(ROWS), years=[2026])

    runner.run_once()

    assert runner.status.last_result not in {"skipped_not_a_trading_day", "skipped_outside_session"}


def test_an_unknown_calendar_never_reports_a_trading_day() -> None:
    assert TradingCalendar.unknown().is_trading_day(dt.date(2026, 10, 1), exchange="NSE") is False


def test_merge_removes_a_withdrawn_holiday_for_a_reported_exchange() -> None:
    """A union can only grow; a holiday the exchange later WITHDRAWS could
    never be removed and the engine would stand down forever on a real
    trading day. The fresh calendar's `closed` set must REPLACE the stale
    one for any exchange it reports — even when that means the fresh set no
    longer contains a date the stale one did."""
    withdrawn = dt.date(2026, 5, 1)
    existing = TradingCalendar(known=True, closed={"NSE": frozenset({withdrawn})})
    fresh = TradingCalendar(known=True, closed={"NSE": frozenset()})  # NSE reported, now empty

    merged = _merge(existing, fresh, year=2026)

    assert merged.is_trading_day(withdrawn, exchange="NSE") is True


def test_merge_keeps_holidays_for_an_exchange_the_fresh_calendar_does_not_report() -> None:
    """An exchange absent from the fresh payload must not silently lose its
    holidays — only exchanges the fresh calendar actually reports should be
    replaced."""
    kept = dt.date(2026, 5, 1)
    existing = TradingCalendar(known=True, closed={"BSE": frozenset({kept})})
    fresh = TradingCalendar(known=True, closed={"NSE": frozenset()})  # says nothing about BSE

    merged = _merge(existing, fresh, year=2026)

    assert merged.is_trading_day(kept, exchange="BSE") is False
