"""Tests for te.broker.instrument_sync — pulls lot sizes/expiry from a
(mocked) broker response and writes them into the `instruments` table."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, select

from te.broker.instrument_sync import fetch_instruments, instruments, sync_instruments
from te.broker.openalgo_rest import OpenAlgoRestClient, OpenAlgoRestError


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._payload


def test_instrument_sync_writes_lot_size_from_broker_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, json: dict[str, Any], timeout: float) -> _FakeResponse:
        assert json["apikey"] == "secret-key"
        return _FakeResponse(
            {
                "status": "success",
                "data": {
                    "symbol": "NIFTY30JUN26FUT",
                    "name": "NIFTY",
                    "exchange": "NFO",
                    "expiry": "30-JUN-26",
                    "instrumenttype": "FUT",
                    "lotsize": 65,
                    "strike": 0,
                    "tick_size": 10,
                },
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OpenAlgoRestClient(host="http://openalgo:5000", api_key="secret-key")
    rows = fetch_instruments(client, [("NIFTY30JUN26FUT", "NFO")])

    assert len(rows) == 1
    assert rows[0].lot_size == 65

    engine = create_engine("sqlite:///:memory:")
    written = sync_instruments(engine, rows)
    assert written == 1

    with engine.connect() as conn:
        result = conn.execute(select(instruments)).fetchall()

    assert len(result) == 1
    assert result[0].lot_size == 65
    assert result[0].symbol == "NIFTY30JUN26FUT"
    engine.dispose()


def test_a_response_with_no_lotsize_is_refused_not_synced(monkeypatch: pytest.MonkeyPatch) -> None:
    """A symbol-service response that omits `lotsize` must never sync a row
    at all — the freeze-quantity-of-1 bug wearing a different hat. Both
    `test_instrument_sync_writes_lot_size_from_broker_response` and
    `test_sync_instruments_upserts_on_repeat_sync` always supply
    `"lotsize": 65` explicitly, so the omitted-field path was never
    exercised."""

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> _FakeResponse:
        return _FakeResponse(
            {
                "status": "success",
                "data": {
                    "symbol": "NIFTY30JUN26FUT",
                    "name": "NIFTY",
                    "exchange": "NFO",
                    "expiry": "30-JUN-26",
                    "instrumenttype": "FUT",
                    # "lotsize" deliberately omitted
                    "strike": 0,
                    "tick_size": 10,
                },
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OpenAlgoRestClient(host="http://openalgo:5000", api_key="secret-key")
    with pytest.raises(OpenAlgoRestError, match="no usable lotsize"):
        fetch_instruments(client, [("NIFTY30JUN26FUT", "NFO")])


def test_a_response_with_zero_lotsize_is_refused_not_synced(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored lot size of `0` is not a "missing" signal anywhere
    downstream — `te/engine/state.py`'s inactive-instrument guard only fires
    on `None`, so a real `0` would flow straight into
    `quantity = lots * lot_size`."""

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> _FakeResponse:
        return _FakeResponse(
            {
                "status": "success",
                "data": {
                    "symbol": "NIFTY30JUN26FUT",
                    "name": "NIFTY",
                    "exchange": "NFO",
                    "expiry": "30-JUN-26",
                    "instrumenttype": "FUT",
                    "lotsize": 0,
                    "strike": 0,
                    "tick_size": 10,
                },
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OpenAlgoRestClient(host="http://openalgo:5000", api_key="secret-key")
    with pytest.raises(OpenAlgoRestError, match="no usable lotsize"):
        fetch_instruments(client, [("NIFTY30JUN26FUT", "NFO")])


def test_sync_instruments_upserts_on_repeat_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    from te.broker.openalgo_rest import SymbolMeta

    engine = create_engine("sqlite:///:memory:")
    row_v1 = SymbolMeta(
        symbol="NIFTY30JUN26FUT",
        exchange="NFO",
        name="NIFTY",
        instrument_type="FUT",
        expiry="30-JUN-26",
        strike=0.0,
        lot_size=65,
        tick_size=10.0,
    )
    sync_instruments(engine, [row_v1])

    row_v2 = SymbolMeta(
        symbol="NIFTY30JUN26FUT",
        exchange="NFO",
        name="NIFTY",
        instrument_type="FUT",
        expiry="30-JUN-26",
        strike=0.0,
        lot_size=75,
        tick_size=10.0,  # lot size revised
    )
    sync_instruments(engine, [row_v2])

    with engine.connect() as conn:
        result = conn.execute(select(instruments)).fetchall()

    assert len(result) == 1, "second sync should upsert, not duplicate"
    assert result[0].lot_size == 75
    engine.dispose()
