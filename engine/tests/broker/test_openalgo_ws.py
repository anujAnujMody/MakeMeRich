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
