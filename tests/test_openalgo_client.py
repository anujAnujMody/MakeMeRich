# mypy: ignore-errors
from unittest.mock import patch

import pytest

from engine.openalgo_client import OpenAlgoClient


@pytest.fixture
def live_client() -> OpenAlgoClient:
    return OpenAlgoClient(host="http://localhost:5000", dry_run=False)


@pytest.fixture
def dry_client() -> OpenAlgoClient:
    return OpenAlgoClient(host="http://localhost:5000", dry_run=True)


class TestInit:
    def test_dry_run_flag(self) -> None:
        c = OpenAlgoClient(host="http://localhost:5000", dry_run=True)
        assert c.dry_run is True

    def test_live_mode(self) -> None:
        c = OpenAlgoClient(host="http://localhost:5000", dry_run=False)
        assert c.dry_run is False


class TestFetchOhlcv:
    def test_returns_bars(self, live_client) -> None:
        mock_json = [
            {"time": "2024-01-01T09:15:00+05:30", "open": 100, "high": 105, "low": 95, "close": 101, "volume": 1000},
            {"time": "2024-01-01T09:20:00+05:30", "open": 101, "high": 110, "low": 100, "close": 108, "volume": 2000},
        ]
        with patch("engine.openalgo_client.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = mock_json

            bars = live_client.fetch_ohlcv("BANKNIFTY", "NFO")

            assert len(bars) == 2
            assert bars[0]["close"] == 101
            assert bars[1]["close"] == 108
            mock_get.assert_called_once()

    def test_handles_api_error(self, live_client) -> None:
        with patch("engine.openalgo_client.requests.get") as mock_get:
            mock_get.return_value.status_code = 500
            mock_get.return_value.text = "Server Error"

            bars = live_client.fetch_ohlcv("BANKNIFTY", "NFO")
            assert bars == []

    def test_handles_network_error(self, live_client) -> None:
        with patch("engine.openalgo_client.requests.get") as mock_get:
            mock_get.side_effect = ConnectionError("Failed")

            bars = live_client.fetch_ohlcv("BANKNIFTY", "NFO")
            assert bars == []

    def test_handles_empty_response(self, live_client) -> None:
        with patch("engine.openalgo_client.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = []

            bars = live_client.fetch_ohlcv("BANKNIFTY", "NFO")
            assert bars == []


class TestPlaceOrder:
    def test_live_places_order(self, live_client) -> None:
        with patch("engine.openalgo_client.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"status": "success", "order_id": "ORD123"}

            result = live_client.place_order(
                symbol="BANKNIFTY",
                exchange="NFO",
                direction="BUY",
                quantity=15,
                order_type="LIMIT",
                price=45000,
            )

            assert result["status"] == "success"
            mock_post.assert_called_once()

    def test_live_order_rejection(self, live_client) -> None:
        with patch("engine.openalgo_client.requests.post") as mock_post:
            mock_post.return_value.status_code = 400
            mock_post.return_value.json.return_value = {"status": "error", "message": "Insufficient margin"}

            result = live_client.place_order(
                symbol="BANKNIFTY", exchange="NFO", direction="BUY",
                quantity=15, order_type="LIMIT", price=45000,
            )

            assert result["status"] == "error"

    def test_dry_run_does_not_call_api(self, dry_client) -> None:
        with patch("engine.openalgo_client.requests.post") as mock_post:
            result = dry_client.place_order(
                symbol="BANKNIFTY", exchange="NFO", direction="BUY",
                quantity=15, order_type="LIMIT", price=45000,
            )

            assert result["status"] == "dry_run"
            assert "order_id" in result
            mock_post.assert_not_called()


class TestGetPositions:
    def test_returns_positions(self, live_client) -> None:
        mock_positions = [
            {"symbol": "BANKNIFTY", "quantity": 15, "pnl": 1500},
            {"symbol": "NIFTY", "quantity": -25, "pnl": -500},
        ]
        with patch("engine.openalgo_client.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = mock_positions

            positions = live_client.get_positions()
            assert len(positions) == 2
            assert positions[0]["symbol"] == "BANKNIFTY"

    def test_handles_error(self, live_client) -> None:
        with patch("engine.openalgo_client.requests.get") as mock_get:
            mock_get.return_value.status_code = 500

            positions = live_client.get_positions()
            assert positions == []


class TestGetOrderStatus:
    def test_returns_status(self, live_client) -> None:
        with patch("engine.openalgo_client.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {"status": "complete", "order_id": "ORD123"}

            result = live_client.get_order_status("ORD123")
            assert result["status"] == "complete"

    def test_handles_missing_order(self, live_client) -> None:
        with patch("engine.openalgo_client.requests.get") as mock_get:
            mock_get.return_value.status_code = 404

            result = live_client.get_order_status("INVALID")
            assert result == {}


class TestCancelOrder:
    def test_cancel_success(self, live_client) -> None:
        with patch("engine.openalgo_client.requests.delete") as mock_delete:
            mock_delete.return_value.status_code = 200
            mock_delete.return_value.json.return_value = {"status": "cancelled"}

            result = live_client.cancel_order("ORD123")
            assert result["status"] == "cancelled"

    def test_dry_run_cancel(self, dry_client) -> None:
        with patch("engine.openalgo_client.requests.delete") as mock_delete:
            result = dry_client.cancel_order("ORD123")
            assert result["status"] == "dry_run"
            mock_delete.assert_not_called()
