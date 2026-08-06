import datetime as dt

import pytest

from te.domain.clock import (
    DEFAULT_SESSION,
    IST,
    SessionWindow,
    assume_utc,
    closed_bars_asof,
    is_market_open,
    to_utc,
)


def test_to_utc_converts_aware_timestamp_to_utc() -> None:
    ist = dt.datetime(2026, 7, 29, 9, 15, tzinfo=IST)
    out = to_utc(ist)
    assert out.tzinfo is dt.UTC
    assert out == ist
    assert (out.hour, out.minute) == (3, 45)


def test_to_utc_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timestamp must be timezone-aware"):
        to_utc(dt.datetime(2026, 7, 29, 9, 15))


def test_to_utc_names_the_field_in_the_error() -> None:
    """Call sites that validate a named field (e.g. `params_frozen_at`) keep
    their specific message rather than a generic one."""
    with pytest.raises(ValueError, match="params_frozen_at must be timezone-aware"):
        to_utc(dt.datetime(2026, 7, 29, 9, 15), name="params_frozen_at")


def test_assume_utc_reattaches_utc_to_a_naive_timestamp() -> None:
    """SQLite reads `DateTime(timezone=True)` back tz-naive; the digits are
    already UTC, so reattach rather than convert."""
    naive = dt.datetime(2026, 7, 29, 3, 45)
    out = assume_utc(naive)
    assert out.tzinfo is dt.UTC
    assert out.hour == 3 and out.minute == 45


def test_assume_utc_leaves_an_aware_timestamp_untouched() -> None:
    ist = dt.datetime(2026, 7, 29, 9, 15, tzinfo=IST)
    assert assume_utc(ist) is ist


def test_ist_is_utc_plus_5_30() -> None:
    moment = dt.datetime(2026, 7, 29, 9, 15, tzinfo=IST)
    assert moment.utcoffset() == dt.timedelta(hours=5, minutes=30)


def test_closed_bars_asof_requires_tz_aware() -> None:
    naive = dt.datetime(2026, 7, 29, 9, 20)
    aware = dt.datetime(2026, 7, 29, 9, 20, tzinfo=IST)
    with pytest.raises(ValueError, match="timezone-aware"):
        closed_bars_asof(as_of=naive, event_ts=aware, interval=dt.timedelta(minutes=1))
    with pytest.raises(ValueError, match="timezone-aware"):
        closed_bars_asof(as_of=aware, event_ts=naive, interval=dt.timedelta(minutes=1))


def test_closed_bars_asof_true_when_close_ts_at_or_before_as_of() -> None:
    event_ts = dt.datetime(2026, 7, 29, 9, 15, tzinfo=IST)
    interval = dt.timedelta(minutes=1)
    # close_ts = 09:16 exactly -> closed at as_of == 09:16
    assert closed_bars_asof(as_of=dt.datetime(2026, 7, 29, 9, 16, tzinfo=IST), event_ts=event_ts, interval=interval)
    assert closed_bars_asof(as_of=dt.datetime(2026, 7, 29, 9, 20, tzinfo=IST), event_ts=event_ts, interval=interval)


def test_closed_bars_asof_false_when_bar_has_not_closed_yet() -> None:
    event_ts = dt.datetime(2026, 7, 29, 9, 15, tzinfo=IST)
    interval = dt.timedelta(minutes=1)
    # bar closes at 09:16; at as_of 09:15:30 it hasn't closed
    assert not closed_bars_asof(
        as_of=dt.datetime(2026, 7, 29, 9, 15, 30, tzinfo=IST), event_ts=event_ts, interval=interval
    )


def test_default_session_is_915_to_1530() -> None:
    assert DEFAULT_SESSION.start == dt.time(9, 15)
    assert DEFAULT_SESSION.end == dt.time(15, 30)


def test_is_market_open_uses_configurable_window() -> None:
    custom = SessionWindow(start=dt.time(9, 0), end=dt.time(16, 0))
    before_default_but_within_custom = dt.datetime(2026, 7, 29, 9, 5, tzinfo=IST)
    assert not is_market_open(before_default_but_within_custom, DEFAULT_SESSION)
    assert is_market_open(before_default_but_within_custom, custom)


def test_is_market_open_within_default_session() -> None:
    moment = dt.datetime(2026, 7, 29, 12, 0, tzinfo=IST)
    assert is_market_open(moment)


def test_is_market_open_false_outside_session() -> None:
    moment = dt.datetime(2026, 7, 29, 16, 0, tzinfo=IST)
    assert not is_market_open(moment)
