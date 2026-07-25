import json
import uuid
from typing import Any, cast

import requests


class OpenAlgoClient:
    def __init__(self, host: str = "http://localhost:5000", dry_run: bool = True) -> None:
        self.host = host.rstrip("/")
        self.dry_run = dry_run

    def fetch_ohlcv(self, symbol: str, exchange: str, interval: str = "5m") -> list[dict[str, Any]]:
        url = f"{self.host}/api/history?symbol={symbol}&exchange={exchange}&interval={interval}"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return []
            data = resp.json()
            if isinstance(data, list):
                return data
            raw = data.get("data", [])
            return raw if isinstance(raw, list) else []
        except (requests.RequestException, json.JSONDecodeError, OSError):
            return []

    def place_order(
        self,
        symbol: str,
        exchange: str,
        direction: str,
        quantity: int,
        order_type: str = "MARKET",
        price: float = 0.0,
    ) -> dict[str, Any]:
        if self.dry_run:
            return {"status": "dry_run", "order_id": f"dry-{uuid.uuid4().hex[:8]}", "symbol": symbol}

        payload: dict[str, Any] = {
            "symbol": symbol,
            "exchange": exchange,
            "action": direction,
            "quantity": quantity,
            "ordertype": order_type,
            "price": price,
        }
        try:
            resp = requests.post(f"{self.host}/api/placeorder", json=payload, timeout=10)
            return resp.json() if resp.status_code == 200 else {"status": "error", "message": resp.text}
        except (requests.RequestException, json.JSONDecodeError) as e:
            return {"status": "error", "message": str(e)}

    def get_positions(self) -> list[dict[str, Any]]:
        try:
            resp = requests.get(f"{self.host}/api/positions", timeout=10)
            if resp.status_code != 200:
                return []
            data = resp.json()
            return data if isinstance(data, list) else data.get("data", [])
        except (requests.RequestException, json.JSONDecodeError):
            return []

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        try:
            resp = requests.get(f"{self.host}/api/orderstatus?order_id={order_id}", timeout=10)
            if resp.status_code != 200:
                return {}
            return cast("dict[str, Any]", resp.json())
        except (requests.RequestException, json.JSONDecodeError):
            return {}

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        if self.dry_run:
            return {"status": "dry_run", "order_id": order_id}
        try:
            resp = requests.delete(f"{self.host}/api/cancelorder?order_id={order_id}", timeout=10)
            return resp.json() if resp.status_code == 200 else {"status": "error", "message": resp.text}
        except (requests.RequestException, json.JSONDecodeError) as e:
            return {"status": "error", "message": str(e)}
