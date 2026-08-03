"""A failed option-strike resolution must be retryable and must be VISIBLE.

Found live on 2026-08-03, and this is the whole reason the refresh is on a
timer. The engine restarted before the OpenAlgo gateway was reachable, so
every `optionsymbol` call raised `Connection refused`, `strike_band` returned
`[]` for all four underlyings, and the resolution — a one-shot at recorder
start — gave up. Premium recording stayed off for the entire session. It
returned without even logging, and `is_running()` cheerfully reported a
healthy recorder the whole time, because the FEED was healthy: it was only
the option half that was dead.

So two things are pinned here: re-asking actually recovers, and every
outcome lands somewhere observable rather than in a swallowed exception.
"""

from __future__ import annotations

from te.broker.openalgo_ws import Instrument, OpenAlgoWSClient
from te.engine.scheduler import WSRecorderSupervisor


class _RecordingWSClient(OpenAlgoWSClient):
    """A client whose `set_dynamic_subscriptions` records instead of sending."""

    def __init__(self) -> None:
        super().__init__(url="ws://unused", api_key="unused")
        self.applied: list[list[Instrument]] = []

    async def set_dynamic_subscriptions(self, instruments: list[Instrument]) -> list[Instrument]:
        self.applied.append(list(instruments))
        return list(instruments)


def _supervisor(ws_client: OpenAlgoWSClient, resolve: object) -> WSRecorderSupervisor:
    supervisor = WSRecorderSupervisor(ws_client, recorder=None, late_instruments=resolve)  # type: ignore[arg-type]
    return supervisor


def test_a_refresh_while_the_recorder_is_down_is_recorded_not_silent() -> None:
    """`refresh_late_instruments` is on a 5-minute timer that also ticks
    outside the session. It must no-op then — but say so."""
    ws_client = _RecordingWSClient()
    supervisor = _supervisor(ws_client, lambda: [Instrument(exchange="NFO", symbol="X")])

    supervisor.refresh_late_instruments()

    assert ws_client.applied == [], "must not touch the feed while the recorder is down"
    status = supervisor.late_status
    assert status.error == "recorder is not running"
    assert status.attempted_at is not None, "the attempt itself must be visible"


def test_a_resolution_that_raises_is_visible_in_status() -> None:
    """The 2026-08-03 shape: broker unreachable, every call raises."""

    def _boom() -> list[Instrument]:
        raise ConnectionRefusedError("gateway not up yet")

    supervisor = _supervisor(_RecordingWSClient(), _boom)
    supervisor._loop = object()  # type: ignore[assignment]
    supervisor.is_running = lambda: True  # type: ignore[method-assign]

    supervisor.refresh_late_instruments()

    status = supervisor.late_status
    assert status.subscribed == 0
    assert status.error is not None and "resolve failed" in status.error
    assert "gateway not up yet" in status.error


def test_resolving_nothing_is_an_error_not_a_shrug() -> None:
    """The exact silent path: `strike_band` swallows per-underlying broker
    failures and returns `[]`, so a total outage reaches here as an empty
    list rather than an exception. That used to `return` with no log and no
    trace, which is how a whole day went missing unnoticed."""
    supervisor = _supervisor(_RecordingWSClient(), list)
    supervisor._loop = object()  # type: ignore[assignment]
    supervisor.is_running = lambda: True  # type: ignore[method-assign]

    supervisor.refresh_late_instruments()

    assert supervisor.late_status.error == "resolved no instruments"


def test_never_attempted_is_distinguishable_from_failed() -> None:
    """A fresh supervisor has not tried yet — that must not read as success
    (0 subscribed) or as failure (an error string)."""
    status = _supervisor(_RecordingWSClient(), list).late_status
    assert status.attempted_at is None
    assert status.error is None
    assert status.subscribed == 0
