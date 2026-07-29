"""Minimal OpenAlgo REST client — quotes/history only, no order methods.

Per the plan's Phase 1 scope, this is deliberately narrow: no
`placeorder`/`cancelorder`/basket methods live here (those belong to a later
phase's execution work). OpenAlgo's REST API takes the API key **in the JSON
request body** (not a header) on every call — this is the one thing every
endpoint has in common, so `_post()` centralises it.

Endpoint paths/payload shapes below follow OpenAlgo's documented v1 REST API
(`POST {host}/api/v1/quotes`, `POST {host}/api/v1/history`) — this was NOT
verified against a live OpenAlgo instance in this sandbox (no network
egress). Verify against a running OpenAlgo instance before relying on this
in production; see the accompanying report for exactly what to check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class OpenAlgoRestError(RuntimeError):
    """Raised when OpenAlgo responds with `status != "success"` or the HTTP
    call itself fails.

    `status_code` is the HTTP status when the failure came with one, and
    `None` for an application-level error carried on an HTTP 200 or for a
    transport failure that never produced a response."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    exchange: str
    ltp: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: float
    oi: float
    bid: float
    ask: float


@dataclass(frozen=True, slots=True)
class HistoryBar:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    oi: float


@dataclass(frozen=True, slots=True)
class SymbolMeta:
    """Contract metadata — the authoritative source of lot size, tick size,
    and expiry, per `.agents/skills/openalgo/references/symbol-services.md`.
    Feeds `te/broker/instrument_sync.py`, which is what makes lot sizes
    non-hardcoded (the plan's repeated emphasis: lot sizes change ~every 6
    months and must never be literals in code)."""

    symbol: str
    exchange: str
    name: str  # underlying, e.g. "NIFTY"
    instrument_type: str  # "FUT" | "CE" | "PE"
    expiry: str  # broker's raw "DD-MMM-YY" string
    strike: float
    lot_size: int
    tick_size: float


class OpenAlgoRestClient:
    """Synchronous REST client against an OpenAlgo instance. Kept
    dependency-free of the third-party `openalgo` SDK package (not a pinned
    dependency of this project) — raw `httpx` calls only, per the plan's
    `te/broker/openalgo_rest.py` spec ("apikey in JSON body; typed
    responses")."""

    def __init__(self, host: str, api_key: str, *, timeout: float = 10.0) -> None:
        self._host = host.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POSTs `payload` (plus the apikey) and returns the decoded
        `status: "success"` envelope.

        The response body is parsed BEFORE the status code is judged. On a
        4xx/5xx OpenAlgo still returns its documented error envelope
        (`{"status": "error", "message": "<reason>"}`), and that `message` is
        the only thing that says WHY — invalid key, session expired, rate
        limit, lot-size violation, insufficient margin. Calling
        `raise_for_status()` first (as this used to) discards the body and
        leaves the caller with httpx's generic status text, which is
        undiagnosable."""
        body = {"apikey": self._api_key, **payload}
        url = f"{self._host}{path}"

        try:
            response = httpx.post(url, json=body, timeout=self._timeout)
        except httpx.HTTPError as exc:
            # Transport-level failure — no response, so no body to read.
            raise OpenAlgoRestError(f"OpenAlgo request to {path} failed: {exc}") from exc

        status_code = response.status_code
        try:
            parsed = response.json()
        except Exception:
            # Not JSON at all (a proxy's HTML 502 page, a truncated body...).
            parsed = None
        data: dict[str, Any] = parsed if isinstance(parsed, dict) else {}

        if status_code >= 400:
            reason = data.get("message") or "no error message in response body"
            raise OpenAlgoRestError(
                f"OpenAlgo {path} failed with HTTP {status_code}: {reason}", status_code=status_code
            )

        if data.get("status") != "success":
            raise OpenAlgoRestError(
                f"OpenAlgo {path} returned status={data.get('status')!r}: {data.get('message')}"
            )
        return data

    def quotes(self, symbol: str, exchange: str) -> Quote:
        data = self._post("/api/v1/quotes", {"symbol": symbol, "exchange": exchange})["data"]
        return Quote(
            symbol=symbol,
            exchange=exchange,
            ltp=float(data.get("ltp", 0.0)),
            open=float(data.get("open", 0.0)),
            high=float(data.get("high", 0.0)),
            low=float(data.get("low", 0.0)),
            prev_close=float(data.get("prev_close", 0.0)),
            volume=float(data.get("volume", 0.0)),
            oi=float(data.get("oi", 0.0)),
            bid=float(data.get("bid", 0.0)),
            ask=float(data.get("ask", 0.0)),
        )

    def history(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        start_date: str,
        end_date: str,
    ) -> list[HistoryBar]:
        data = self._post(
            "/api/v1/history",
            {
                "symbol": symbol,
                "exchange": exchange,
                "interval": interval,
                "start_date": start_date,
                "end_date": end_date,
            },
        )["data"]
        return [
            HistoryBar(
                timestamp=str(row.get("timestamp", "")),
                open=float(row.get("open", 0.0)),
                high=float(row.get("high", 0.0)),
                low=float(row.get("low", 0.0)),
                close=float(row.get("close", 0.0)),
                volume=float(row.get("volume", 0.0)),
                oi=float(row.get("oi", 0.0)),
            )
            for row in data
        ]

    def symbol_meta(self, symbol: str, exchange: str) -> SymbolMeta:
        """Resolves one contract's lot size / tick size / expiry via
        OpenAlgo's `symbol` service (`POST /api/v1/symbol`)."""
        data = self._post("/api/v1/symbol", {"symbol": symbol, "exchange": exchange})["data"]
        return SymbolMeta(
            symbol=str(data.get("symbol", symbol)),
            exchange=str(data.get("exchange", exchange)),
            name=str(data.get("name", "")),
            instrument_type=str(data.get("instrumenttype", "")),
            expiry=str(data.get("expiry", "")),
            strike=float(data.get("strike", 0.0)),
            lot_size=int(data.get("lotsize", 0)),
            tick_size=float(data.get("tick_size", 0.0)),
        )
