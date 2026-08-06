"""Verifies ticks are actually arriving, not just that the WS thread is
alive — see `te.engine.scheduler.FeedHealthStatus` for the exact incident
this closes: 2026-08-04, an ad-hoc broker relogin made OpenAlgo delete its
Angel WS adapter without dropping this engine's own WS connection, so
`is_running()` stayed `True` while zero ticks arrived for about an hour
before a human noticed by hand and bounced the recorder themselves.
"""

from __future__ import annotations

import datetime as dt

from te.broker.openalgo_ws import OpenAlgoWSClient
from te.engine.scheduler import WSRecorderSupervisor


class _FakeRecorder:
    def __init__(self, *, last_tick_at: dt.datetime | None) -> None:
        self.last_tick_at = last_tick_at


def _supervisor(
    *, last_tick_at: dt.datetime | None, started_at: dt.datetime | None, running: bool
) -> tuple[WSRecorderSupervisor, list[str]]:
    ws_client = OpenAlgoWSClient(url="ws://unused", api_key="unused")
    recorder = _FakeRecorder(last_tick_at=last_tick_at)
    supervisor = WSRecorderSupervisor(ws_client, recorder=recorder)  # type: ignore[arg-type]
    supervisor._started_at = started_at
    supervisor.is_running = lambda: running  # type: ignore[method-assign]
    calls: list[str] = []
    supervisor.stop = lambda: calls.append("stop")  # type: ignore[method-assign]
    supervisor.start = lambda: calls.append("start")  # type: ignore[method-assign]
    return supervisor, calls


def test_a_stopped_recorder_is_not_checked() -> None:
    """Outside the session `is_running()` is legitimately `False` — must
    stay quiet, matching `refresh_late_instruments`'s convention."""
    supervisor, calls = _supervisor(last_tick_at=None, started_at=None, running=False)
    supervisor.check_feed_health()
    assert calls == []
    assert supervisor.feed_health.checked_at is None


def test_a_freshly_started_recorder_gets_a_grace_period() -> None:
    """No ticks yet is normal in the first few seconds after `start()` —
    flagging that as stale would bounce a perfectly healthy feed before it
    had any chance to prove itself."""
    now = dt.datetime.now(dt.UTC)
    supervisor, calls = _supervisor(last_tick_at=None, started_at=now, running=True)
    supervisor.check_feed_health(stale_after=dt.timedelta(minutes=3))
    assert calls == []
    assert supervisor.feed_health.checked_at is None


def test_recent_ticks_are_healthy_and_left_alone() -> None:
    now = dt.datetime.now(dt.UTC)
    supervisor, calls = _supervisor(
        last_tick_at=now - dt.timedelta(seconds=10),
        started_at=now - dt.timedelta(minutes=10),
        running=True,
    )
    supervisor.check_feed_health(stale_after=dt.timedelta(minutes=3))
    assert calls == []
    assert supervisor.feed_health.healed is False
    assert supervisor.feed_health.last_tick_at is not None


def test_a_stale_feed_is_bounced_not_just_logged() -> None:
    """The exact 2026-08-04 shape: the WS thread is alive (`is_running()`
    `True`) but no tick has landed in a long time. The check must reconnect
    itself, not merely record the fact and wait for a human."""
    now = dt.datetime.now(dt.UTC)
    supervisor, calls = _supervisor(
        last_tick_at=now - dt.timedelta(minutes=20),
        started_at=now - dt.timedelta(minutes=25),
        running=True,
    )
    supervisor.check_feed_health(stale_after=dt.timedelta(minutes=3))
    assert calls == ["stop", "start"]
    assert supervisor.feed_health.healed is True


def test_a_feed_that_has_never_ticked_after_the_grace_period_is_also_stale() -> None:
    """Distinct from the freshly-started case: once the grace period has
    fully elapsed and still nothing has arrived, that is exactly as stale as
    ticks having stopped — `last_tick_at is None` must not default to
    healthy."""
    now = dt.datetime.now(dt.UTC)
    supervisor, calls = _supervisor(
        last_tick_at=None,
        started_at=now - dt.timedelta(minutes=10),
        running=True,
    )
    supervisor.check_feed_health(stale_after=dt.timedelta(minutes=3))
    assert calls == ["stop", "start"]
    assert supervisor.feed_health.healed is True


def test_never_checked_is_distinguishable_from_healthy() -> None:
    """A fresh supervisor has not run a check yet — that must not read as
    healthy (which would need a real `checked_at`)."""
    status = _supervisor(last_tick_at=None, started_at=None, running=False)[0].feed_health
    assert status.checked_at is None
    assert status.healed is False
