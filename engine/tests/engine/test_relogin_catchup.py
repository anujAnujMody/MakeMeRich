"""The broker login must not be lost when the 08:40 cron does not fire.

Angel expires its broker session nightly, so without a successful login the
engine cannot fetch a single quote. On 2026-08-05 the 08:40 cron did not
fire: every quote returned HTTP 500 "Failed to fetch LTP" through the whole
pre-open, and recovery took a hand-run `POST /api/engine/relogin-broker` at
08:55. Nothing in the system noticed or complained.

This is the SECOND time this exact shape has cost a session. The WS recorder
lost 2026-07-30 the same way, and `should_start_recorder_now` was written for
it — a once-a-day `CronTrigger` plus a process that restarts is a job that
silently never runs, because `BackgroundScheduler` has no memory across
processes and cannot tell "already fired today" from "never fired".

Two independent failure modes, both covered here:

* the process was not up at 08:40 (redeploy, crash) — the catch-up below
* the scheduler was busy at 08:40 — `misfire_grace_time`, since APScheduler's
  default grace is ONE SECOND

The reason a restart-triggered relogin was refused originally is real and is
tested too: a dev `--reload` loop must not replay live credentials at Angel's
rate limiter on every reload. `last_success` is what makes it safe, and
`test_a_second_restart_on_the_same_day_does_not_log_in_again` is the test
that says so.
"""

from __future__ import annotations

import datetime as dt

import pytest

from te.domain.clock import IST
from te.engine.scheduler import relogin_is_overdue

MONDAY = dt.date(2026, 8, 10)
SATURDAY = dt.date(2026, 8, 8)


def _at(day: dt.date, hour: int, minute: int) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=IST)


def test_the_exact_2026_08_05_failure_is_caught() -> None:
    """08:55 on a weekday, no login yet today — precisely the state the
    engine was in while every quote failed, and it must now self-heal
    instead of waiting for a human."""
    assert relogin_is_overdue(_at(MONDAY, 8, 55), None) is True


def test_a_restart_hours_into_the_session_still_logs_in() -> None:
    """The window runs to the close, not to the open. A login at 11:00 saves
    the rest of the day; refusing it because "the moment has passed" leaves
    the engine unable to price a single position."""
    assert relogin_is_overdue(_at(MONDAY, 11, 0), None) is True


def test_a_second_restart_on_the_same_day_does_not_log_in_again() -> None:
    """The guard that makes this safe to run on EVERY start.

    A dev `--reload` loop restarts the process constantly; replaying real
    credentials against Angel each time would hit their rate limiter for no
    reason, which is exactly why the original design refused to trigger a
    relogin on restart. Gating on "already succeeded today" removes that
    objection rather than accepting it.
    """
    assert relogin_is_overdue(_at(MONDAY, 9, 30), MONDAY) is False


def test_yesterdays_login_does_not_count() -> None:
    """The broker session expires NIGHTLY. A stale success must not suppress
    today's — that would reproduce the original bug with an extra step."""
    assert relogin_is_overdue(_at(MONDAY, 8, 55), MONDAY - dt.timedelta(days=3)) is True
    assert relogin_is_overdue(_at(MONDAY, 8, 55), MONDAY - dt.timedelta(days=1)) is True


def test_before_the_cron_time_it_is_not_yet_due() -> None:
    """08:39 is not late — the scheduled job is about to run. Firing here
    would double every login, and on a restart loop before 08:40 it would
    log in repeatedly with no `last_success` yet recorded to stop it."""
    assert relogin_is_overdue(_at(MONDAY, 8, 39), None) is False


def test_after_the_close_it_is_no_longer_due() -> None:
    """Nothing left to trade or price today, and tomorrow's cron will handle
    tomorrow. A login here would only burn a request."""
    assert relogin_is_overdue(_at(MONDAY, 16, 0), None) is False


def test_weekends_never_log_in() -> None:
    """No session, no broker to log in to — and a restart on a Saturday
    afternoon must not stamp a success that then suppresses Monday."""
    assert relogin_is_overdue(_at(SATURDAY, 10, 0), None) is False


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (8, 39, False),
        (8, 40, True),  # inclusive lower edge — the cron's own minute
        (15, 29, True),
        (15, 31, False),
    ],
)
def test_the_window_edges(hour: int, minute: int, expected: bool) -> None:
    """Pins the boundaries. Without these, widening or narrowing the window
    by a minute would pass silently, and the lower edge in particular is the
    one that decides whether a restart AT 08:40 is covered by the catch-up or
    left to a cron that may already have missed."""
    assert relogin_is_overdue(_at(MONDAY, hour, minute), None) is expected
