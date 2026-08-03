"""`set_dynamic_subscriptions` must reach a LIVE connection, not wait for a drop.

Found on 2026-08-03: the recorder subscribed to the four index symbols and
nothing else, so the bar store held no option premiums at all — while every
backtest in the engine scores strategies on premiums. The only premium
source was the exchange's once-a-day bhavcopy, so a day's trading could not
be measured until the next morning.

Recording a strike band fixes the coverage, but a band costs one broker
round trip per strike to resolve. That cannot sit in front of the connection
carrying the index bars, so the strikes have to join a feed that is already
up — which `subscribe()` alone cannot do, since it only replays on the next
connect.

The set REPLACES rather than accumulates, because option symbols are dated:
today's weekly is dead tomorrow, and a process left running would otherwise
replay every strike it ever resolved on each reconnect.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
import websockets

from te.broker.openalgo_ws import Instrument, OpenAlgoWSClient


async def _run_client_until(*, initial: list[Instrument], after_connect: list[Instrument]) -> list[dict[str, object]]:
    """Serves one connection, lets the client hand over `initial`, then adds
    `after_connect` WITHOUT dropping the socket. Returns every frame the
    server saw."""
    received: list[dict[str, object]] = []
    handshake_done = asyncio.Event()
    late_frames_seen = asyncio.Event()
    # Waits for the LAST added symbol rather than a frame count. A count
    # would have to restate `set_dynamic_subscriptions`' own dedup rule
    # inside the test that exists to check it, so the oracle would move
    # whenever the rule moved. Frames are sent in list order, so any wrongly
    # re-sent duplicate necessarily arrives BEFORE this one — the duplicate
    # assertions keep their full power.
    last_added = after_connect[-1].symbol

    async def handler(websocket: object) -> None:
        try:
            async for raw in websocket:  # type: ignore[attr-defined]
                received.append(json.loads(raw))
                if len(received) == len(initial) + 1:
                    handshake_done.set()
                elif received[-1].get("symbol") == last_added:
                    late_frames_seen.set()
        except websockets.exceptions.ConnectionClosed:
            return

    server = await websockets.serve(handler, "localhost", 0)
    try:
        port = server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        client = OpenAlgoWSClient(url=f"ws://localhost:{port}", api_key="secret-key")
        client.subscribe(initial)

        task = asyncio.create_task(client.run(on_tick=lambda _msg: None, max_retries=10))
        await asyncio.wait_for(handshake_done.wait(), timeout=10.0)

        await client.set_dynamic_subscriptions(after_connect)
        await asyncio.wait_for(late_frames_seen.wait(), timeout=10.0)

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    finally:
        server.close()
        await server.wait_closed()
    return received


@pytest.mark.asyncio
async def test_options_join_a_live_feed_without_a_reconnect() -> None:
    """The whole point: an option strike resolved a minute after the feed
    came up must start streaming on THAT connection."""
    received = await _run_client_until(
        initial=[Instrument(exchange="NSE_INDEX", symbol="NIFTY")],
        after_connect=[Instrument(exchange="NFO", symbol="NIFTY04AUG2624600CE")],
    )

    subscribed = [f["symbol"] for f in received if f["action"] == "subscribe"]
    assert subscribed == ["NIFTY", "NIFTY04AUG2624600CE"], f"the option did not reach the live connection: {received}"
    # Exactly one connection — no drop was needed to deliver it.
    assert sum(1 for f in received if f["action"] == "authenticate") == 1


@pytest.mark.asyncio
async def test_a_late_instrument_is_replayed_on_reconnect_too() -> None:
    """Registration must outlive the connection it was added on, or a drop
    right after resolution would silently end option recording for the day."""
    client = OpenAlgoWSClient(url="ws://unused", api_key="secret-key")
    client.subscribe([Instrument(exchange="NSE_INDEX", symbol="NIFTY")])

    option = Instrument(exchange="NFO", symbol="NIFTY04AUG2624600CE")
    # No live connection — this is the "resolved during a reconnect" case.
    await client.set_dynamic_subscriptions([option])

    sent: list[dict[str, object]] = []

    class _FakeWS:
        async def send(self, message: str) -> None:
            sent.append(json.loads(message))

    await client._resubscribe(_FakeWS())  # type: ignore[arg-type]
    assert [f["symbol"] for f in sent] == ["NIFTY", "NIFTY04AUG2624600CE"]


@pytest.mark.asyncio
async def test_yesterdays_strikes_are_not_replayed_after_a_refresh() -> None:
    """The set is REPLACED, not accumulated.

    Option symbols carry their expiry in the name, so an append-only
    registration would replay every dead contract it ever saw on each
    reconnect — ~87 more of them per day the process stays up, ahead of the
    strikes that actually matter. The index subscriptions are permanent and
    must survive regardless.
    """
    client = OpenAlgoWSClient(url="ws://unused", api_key="secret-key")
    client.subscribe([Instrument(exchange="NSE_INDEX", symbol="NIFTY")])

    yesterday = Instrument(exchange="NFO", symbol="NIFTY04AUG2624600CE")
    today = Instrument(exchange="NFO", symbol="NIFTY11AUG2624800CE")
    await client.set_dynamic_subscriptions([yesterday])
    await client.set_dynamic_subscriptions([today])

    sent: list[dict[str, object]] = []

    class _FakeWS:
        async def send(self, message: str) -> None:
            sent.append(json.loads(message))

    await client._resubscribe(_FakeWS())  # type: ignore[arg-type]
    symbols = [f["symbol"] for f in sent]
    assert symbols == ["NIFTY", "NIFTY11AUG2624800CE"], f"expired strike replayed on reconnect: {symbols}"


@pytest.mark.asyncio
async def test_an_already_subscribed_instrument_is_not_sent_twice() -> None:
    """Recorder starts are not once-per-process — a same-day restart calls
    `start()` again. Re-resolving the same band must not re-send the whole
    list onto a healthy connection."""
    received = await _run_client_until(
        initial=[Instrument(exchange="NSE_INDEX", symbol="NIFTY")],
        after_connect=[
            Instrument(exchange="NSE_INDEX", symbol="NIFTY"),  # already subscribed
            Instrument(exchange="NFO", symbol="NIFTY04AUG2624600CE"),
        ],
    )
    subscribed = [f["symbol"] for f in received if f["action"] == "subscribe"]
    assert subscribed.count("NIFTY") == 1, f"duplicate subscribe for NIFTY: {received}"
