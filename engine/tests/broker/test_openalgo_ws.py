"""Tests for te.broker.openalgo_ws.OpenAlgoWSClient — reconnect must
re-authenticate AND re-subscribe, never just one of the two."""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
import websockets

from te.broker.openalgo_ws import Instrument, OpenAlgoWSClient


@pytest.mark.asyncio
async def test_ws_reconnect_reauths_and_resubscribes() -> None:
    frames_by_connection: list[list[dict[str, object]]] = []
    second_connection_handshake_done = asyncio.Event()

    async def handler(websocket: object) -> None:
        frames: list[dict[str, object]] = []
        frames_by_connection.append(frames)
        connection_index = len(frames_by_connection)
        try:
            async for raw in websocket:  # type: ignore[attr-defined]
                frames.append(json.loads(raw))
                if connection_index == 1 and len(frames) == 2:
                    # Simulate a mid-session drop right after the client
                    # finishes its initial auth+subscribe handshake.
                    await websocket.close()  # type: ignore[attr-defined]
                    return
                if connection_index == 2 and len(frames) == 2:
                    second_connection_handshake_done.set()
        except websockets.exceptions.ConnectionClosed:
            return

    server = await websockets.serve(handler, "localhost", 0)
    try:
        port = server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        client = OpenAlgoWSClient(url=f"ws://localhost:{port}", api_key="secret-key")
        client.subscribe([Instrument(exchange="NSE_INDEX", symbol="NIFTY")])

        task = asyncio.create_task(client.run(on_tick=lambda _msg: None, max_retries=10))
        await asyncio.wait_for(second_connection_handshake_done.wait(), timeout=10.0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    finally:
        server.close()
        await server.wait_closed()

    assert len(frames_by_connection) >= 2, "client did not reconnect after the simulated drop"

    for frames in frames_by_connection[:2]:
        actions = [f["action"] for f in frames]
        assert "authenticate" in actions, f"connection missing re-auth: {frames}"
        assert "subscribe" in actions, f"connection missing re-subscribe: {frames}"

    assert sum(1 for f in client.sent_frames if f["action"] == "authenticate") >= 2
    assert sum(1 for f in client.sent_frames if f["action"] == "subscribe") >= 2


@pytest.mark.asyncio
async def test_a_raising_on_tick_handler_does_not_kill_the_connection() -> None:
    """Regression, found live on 2026-07-30: `on_tick` ultimately calls
    `BarStore.append()`, and a write failure used to propagate all the way
    out of `run()`'s loop, silently ending tick processing for the rest of
    the session (the supervisor's dedicated thread kept running via
    `loop.run_forever()`, so nothing crashed and nothing restarted — bar
    recording just stopped). One bad tick must never take down every tick
    after it."""
    both_ticks_delivered = asyncio.Event()

    async def handler(websocket: object) -> None:
        async for raw in websocket:  # type: ignore[attr-defined]
            frame = json.loads(raw)
            if frame["action"] == "subscribe":
                await websocket.send(  # type: ignore[attr-defined]
                    json.dumps({"type": "market_data", "data": {"symbol": "NIFTY", "ltp": 100}})
                )
                await websocket.send(  # type: ignore[attr-defined]
                    json.dumps({"type": "market_data", "data": {"symbol": "NIFTY", "ltp": 101}})
                )

    server = await websockets.serve(handler, "localhost", 0)
    received: list[dict[str, object]] = []

    def _on_tick(message: dict[str, object]) -> None:
        received.append(message)
        if len(received) == 1:
            raise RuntimeError("simulated BarStore.append() failure on the first tick")
        if len(received) == 2:
            both_ticks_delivered.set()

    try:
        port = server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        client = OpenAlgoWSClient(url=f"ws://localhost:{port}", api_key="secret-key")
        client.subscribe([Instrument(exchange="NSE_INDEX", symbol="NIFTY")])

        task = asyncio.create_task(client.run(on_tick=_on_tick, max_retries=10))
        await asyncio.wait_for(both_ticks_delivered.wait(), timeout=10.0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    finally:
        server.close()
        await server.wait_closed()

    assert len(received) == 2, "the second tick must still reach on_tick after the first one raised"
