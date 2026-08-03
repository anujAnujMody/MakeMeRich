"""WebSocket client for OpenAlgo's live feed (port 8765 by default).

The one bug class this module exists to structurally prevent: reconnect
logic that re-authenticates but forgets to re-subscribe (subscriptions are
per-session on OpenAlgo's WS server — see
`.agents/skills/openalgo/references/websocket-streaming.md#heartbeat-and-reconnection-low-level`).
Every `(re)connect` in this client sends BOTH an auth frame AND one
subscribe frame per registered instrument, in that order, before yielding
control to the tick loop.

Raw frame shapes (documented, not SDK-wrapped — the `openalgo` SDK package
itself is not a dependency of this project):

    auth:      {"action": "authenticate", "api_key": "..."}
    subscribe: {"action": "subscribe", "mode": <1|2|3>, "symbol": "...", "exchange": "..."}

The exact subscribe-frame key names are an inference from the documented
auth-frame shape and the SDK's `subscribe_ltp/_quote/_depth(instruments)`
signature (mode 1/2/3 = LTP/Quote/Depth) — NOT independently confirmed
against OpenAlgo's server source or a live capture in this sandbox. Verify
against a running OpenAlgo instance (or its `websocket_proxy` source) before
depending on this for live recording.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

TickHandler = Callable[[dict[str, Any]], None]


class _WSConnection(Protocol):
    async def send(self, message: str) -> None: ...

    def __aiter__(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class Instrument:
    exchange: str
    symbol: str


@dataclass
class OpenAlgoWSClient:
    """Maintains a persistent subscription to the OpenAlgo WS feed,
    re-authenticating and re-subscribing on every connect (initial AND
    every reconnect after a drop)."""

    url: str
    api_key: str
    mode: int = 2  # 1=LTP, 2=Quote, 3=Depth
    reconnect_backoff_sec: tuple[float, ...] = (0.1, 0.2, 0.5, 1.0, 2.0)

    #: PERMANENT registrations, added to and never removed — the index
    #: symbols, which are the same every session.
    _subscriptions: list[Instrument] = field(default_factory=list, init=False)
    #: REPLACEABLE registrations, wholly superseded by each
    #: `set_dynamic_subscriptions` call. Option strikes live here because
    #: they are dated: today's weekly is dead tomorrow. Held append-only
    #: alongside the indices they would accumulate ~87 expired symbols per
    #: day in a process left running across sessions, and every reconnect
    #: would replay the lot before reaching today's real strikes.
    _dynamic: list[Instrument] = field(default_factory=list, init=False)
    #: The connection `run()` is currently reading, or `None` between
    #: connects — the handle `set_dynamic_subscriptions` needs to reach a
    #: live feed.
    _live_ws: _WSConnection | None = field(default=None, init=False)
    #: every frame ever sent, in order — lets tests/observability assert
    #: both auth AND subscribe frames were (re-)sent on each connect.
    sent_frames: list[dict[str, Any]] = field(default_factory=list, init=False)

    def subscribe(self, instruments: list[Instrument]) -> None:
        """Registers instruments to (re)subscribe to on every connect. Safe
        to call before `run()` or while it's running (next reconnect will
        pick up new entries)."""
        for inst in instruments:
            if inst not in self._subscriptions:
                self._subscriptions.append(inst)

    async def set_dynamic_subscriptions(self, instruments: list[Instrument]) -> list[Instrument]:
        """REPLACES the dynamic set, subscribing whatever is newly added on
        the CURRENT connection. Returns the instruments actually sent.

        `subscribe()` alone only takes effect on the next connect, which
        makes it unusable for anything resolved after the feed is already
        up. Option strikes are exactly that: resolving a band of them costs
        one broker round trip per strike, far too slow to sit in front of
        the connection that records the index bars. So the feed connects on
        the indices immediately and the strikes join it here, a minute or so
        later, instead of delaying everything.

        Replacement rather than addition so that a set which changes daily
        (or intraday, as the at-the-money strike drifts) cannot accumulate:
        what is registered is always the last set asked for, so a reconnect
        replays today's strikes and not every strike ever resolved.

        Registration happens even when no connection is live, so a
        resolution that lands during a reconnect is picked up by
        `_resubscribe` rather than lost.

        Superseded instruments are dropped from the registration but NOT
        unsubscribed on the live socket: this client speaks the raw
        protocol, and while the SDK exposes `unsubscribe_*`, the raw frame
        shape for it is undocumented (see this module's header on how far
        the subscribe frame itself is already an inference). Guessing a
        second frame to save a handful of redundant ticks until the next
        reconnect is the worse trade.
        """
        already = set(self._subscriptions) | set(self._dynamic)
        self._dynamic = list(instruments)
        fresh = [inst for inst in instruments if inst not in already]
        ws = self._live_ws
        if ws is None:
            return []
        await self._send_subscribes(ws, fresh)
        return fresh

    async def _authenticate(self, ws: _WSConnection) -> None:
        frame = {"action": "authenticate", "api_key": self.api_key}
        await ws.send(json.dumps(frame))
        self.sent_frames.append(frame)

    async def _send_subscribes(self, ws: _WSConnection, instruments: list[Instrument]) -> None:
        """One subscribe frame per instrument, recorded in `sent_frames`.

        Shared by the connect-time replay and by live additions so the frame
        shape is written once: this module's own header flags these key names
        as INFERRED from OpenAlgo's docs rather than confirmed against a
        running server, so the correction most likely to be needed here must
        not have two places to land.
        """
        for inst in instruments:
            frame = {"action": "subscribe", "mode": self.mode, "symbol": inst.symbol, "exchange": inst.exchange}
            await ws.send(json.dumps(frame))
            self.sent_frames.append(frame)

    async def _resubscribe(self, ws: _WSConnection) -> None:
        """Permanent registrations first, then the current dynamic set — so
        a reconnect always restores the indices even if the option strikes
        are mid-refresh."""
        await self._send_subscribes(ws, [*self._subscriptions, *self._dynamic])

    async def run(self, on_tick: TickHandler, *, max_retries: int | None = None) -> None:
        """Connects, authenticates, subscribes, forwards every
        `type == "market_data"` frame to `on_tick`, and transparently
        reconnects (re-auth + re-subscribe, always both) on any connection
        drop. Runs until `max_retries` consecutive connection failures (if
        set) or cancellation.

        `on_tick` exceptions are caught per-message and logged, never
        allowed to escape this loop. Found live: `on_tick` ultimately calls
        `BarStore.append()`, and a single write failure (a real incident,
        see `te.data.barstore`'s non-atomic-write fix) used to propagate
        all the way out of this coroutine — killing the task while
        `WSRecorderSupervisor`'s dedicated thread kept running
        (`loop.run_forever()` has nothing left to run), so recording
        silently stopped for the rest of the session with no crash, no
        restart, and no signal anywhere. One bad tick must never take down
        every tick after it."""
        attempt = 0
        while True:
            try:
                async with websockets.connect(self.url) as ws:
                    await self._authenticate(ws)
                    await self._resubscribe(ws)
                    # Published only AFTER the replay above, so a concurrent
                    # `add_subscriptions` cannot interleave its frames with
                    # the initial batch.
                    self._live_ws = ws
                    attempt = 0
                    try:
                        async for raw in ws:
                            message = json.loads(raw)
                            if message.get("type") == "market_data":
                                try:
                                    on_tick(message)
                                except Exception:
                                    logger.exception("on_tick handler raised — dropping this tick, connection stays up")
                    finally:
                        self._live_ws = None
            except (ConnectionClosed, OSError):
                attempt += 1
                if max_retries is not None and attempt > max_retries:
                    raise
                delay = self.reconnect_backoff_sec[min(attempt - 1, len(self.reconnect_backoff_sec) - 1)]
                await asyncio.sleep(delay)
                continue
