"""Tests for te.broker.openalgo_rest.OpenAlgoRestClient — apikey-in-body
POST semantics and typed quote/history parsing, against a monkeypatched
`httpx.post` (no live network in this sandbox)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from te.broker.openalgo_rest import OpenAlgoRestClient, OpenAlgoRestError


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._payload


def test_quotes_sends_apikey_in_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({"status": "success", "data": {"ltp": 24211.5, "open": 24100.0}})

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OpenAlgoRestClient(host="http://openalgo:5000", api_key="secret-key")
    quote = client.quotes(symbol="NIFTY", exchange="NSE_INDEX")

    assert captured["url"] == "http://openalgo:5000/api/v1/quotes"
    assert captured["json"]["apikey"] == "secret-key"
    assert captured["json"]["symbol"] == "NIFTY"
    assert quote.ltp == 24211.5
    assert quote.open == 24100.0


def test_quotes_raises_on_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, json: dict[str, Any], timeout: float) -> _FakeResponse:
        return _FakeResponse({"status": "error", "message": "invalid symbol"})

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OpenAlgoRestClient(host="http://openalgo:5000", api_key="secret-key")
    with pytest.raises(OpenAlgoRestError, match="invalid symbol"):
        client.quotes(symbol="BOGUS", exchange="NSE_INDEX")


def test_http_error_response_body_message_reaches_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """`raise_for_status()` used to run BEFORE `.json()`, so on any 4xx/5xx
    the documented `{"status":"error","message":"<reason>"}` body was thrown
    away and only httpx's generic status text survived. The actual
    diagnostic reason — invalid key, session expired, rate limit, lot-size
    violation, insufficient margin — must reach the caller."""

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> _FakeResponse:
        return _FakeResponse(
            {"status": "error", "message": "Invalid openalgo apikey"}, status_code=403
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OpenAlgoRestClient(host="http://openalgo:5000", api_key="wrong-key")
    with pytest.raises(OpenAlgoRestError) as excinfo:
        client.quotes(symbol="NIFTY", exchange="NSE_INDEX")

    assert "Invalid openalgo apikey" in str(excinfo.value)
    assert "403" in str(excinfo.value), "the original HTTP status code must be preserved too"
    assert excinfo.value.status_code == 403


def test_http_error_with_a_non_json_body_still_raises_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A proxy/gateway 502 typically returns HTML, not OpenAlgo's JSON error
    envelope. Parsing must degrade gracefully, not raise a JSONDecodeError
    out of the client."""

    class _HtmlResponse(_FakeResponse):
        def json(self) -> dict[str, Any]:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> _HtmlResponse:
        return _HtmlResponse({}, status_code=502)

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OpenAlgoRestClient(host="http://openalgo:5000", api_key="secret-key")
    with pytest.raises(OpenAlgoRestError) as excinfo:
        client.quotes(symbol="NIFTY", exchange="NSE_INDEX")

    assert "502" in str(excinfo.value)
    assert excinfo.value.status_code == 502


def test_successful_response_carries_no_status_code_on_the_error_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """An application-level `status: error` on an HTTP 200 has no failing
    status code to report."""

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> _FakeResponse:
        return _FakeResponse({"status": "error", "message": "invalid symbol"}, status_code=200)

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OpenAlgoRestClient(host="http://openalgo:5000", api_key="secret-key")
    with pytest.raises(OpenAlgoRestError) as excinfo:
        client.quotes(symbol="BOGUS", exchange="NSE_INDEX")

    assert excinfo.value.status_code is None
    assert "invalid symbol" in str(excinfo.value)


def test_history_parses_bar_list(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, json: dict[str, Any], timeout: float) -> _FakeResponse:
        return _FakeResponse(
            {
                "status": "success",
                "data": [
                    {
                        "timestamp": "2026-07-29T09:15:00+05:30",
                        "open": 100,
                        "high": 101,
                        "low": 99,
                        "close": 100.5,
                        "volume": 1000,
                        "oi": 0,
                    },
                ],
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OpenAlgoRestClient(host="http://openalgo:5000", api_key="secret-key")
    bars = client.history(
        symbol="NIFTY",
        exchange="NSE_INDEX",
        interval="1m",
        start_date="2026-07-29",
        end_date="2026-07-29",
    )

    assert len(bars) == 1
    assert bars[0].close == 100.5
