"""Regression for a real bug found live on 2026-07-31: `/api/market-status`
was a permanent Phase-0 stub — `status="closed", label="Unknown"` no matter
the real time — missed by the earlier Tier-0 dashboard-wiring pass (which
fixed dashboard/pnl/trades/positions/orders but not this router). It now
computes the real IST session state from `te.domain.clock`."""

from __future__ import annotations

import datetime as dt

from te.api.routers.market import _compute_market_session
from te.domain.clock import IST


def _at(year: int, month: int, day: int, hour: int, minute: int) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=IST)


def test_reports_open_during_the_regular_session() -> None:
    # 2026-07-31 is a Friday.
    session = _compute_market_session(_at(2026, 7, 31, 10, 0))
    assert session.status == "open"
    assert session.label == "Market open"
    assert session.nextEvent == "2026-07-31T15:30:00+05:30"


def test_reports_pre_open_before_915_ist_on_a_weekday() -> None:
    session = _compute_market_session(_at(2026, 7, 31, 9, 0))
    assert session.status == "pre-open"
    assert session.label == "Market opens soon"
    assert session.nextEvent == "2026-07-31T09:15:00+05:30"


def test_reports_closed_after_1530_ist_on_a_weekday() -> None:
    session = _compute_market_session(_at(2026, 7, 31, 16, 0))
    assert session.status == "closed"
    assert session.label == "Market closed"
    # Next trading day after Friday is Monday.
    assert session.nextEvent == "2026-08-03T09:15:00+05:30"


def test_reports_closed_on_a_weekend() -> None:
    # 2026-08-01 is a Saturday.
    session = _compute_market_session(_at(2026, 8, 1, 12, 0))
    assert session.status == "closed"
    assert "weekend" in session.label.lower()
    assert session.nextEvent == "2026-08-03T09:15:00+05:30"
