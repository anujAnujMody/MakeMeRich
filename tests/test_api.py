# mypy: ignore-errors
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from engine.api import app
from strategies.base import ExitReason, TradeDirection, TradeOutcome, TradeRecord

IST = timezone(timedelta(hours=5, minutes=30))


def _mock_trades() -> list[TradeRecord]:
    return [
        TradeRecord(
            id="t1", strategy="orbs", symbol="BANKNIFTY", exchange="NFO",
            direction=TradeDirection.LONG,
            entry_time=datetime(2024, 1, 2, 9, 30, tzinfo=IST),
            entry_price=45000.0,
            exit_time=datetime(2024, 1, 2, 10, 0, tzinfo=IST),
            exit_price=45300.0, quantity=15, lot_size=15,
            sl_price=44900.0, target_price=45300.0,
            exit_reason=ExitReason.TARGET, pnl=4500.0, outcome=TradeOutcome.WIN,
            range_high=45100.0, range_low=44900.0,
        ),
        TradeRecord(
            id="t2", strategy="orbs", symbol="NIFTY", exchange="NFO",
            direction=TradeDirection.SHORT,
            entry_time=datetime(2024, 1, 2, 9, 45, tzinfo=IST),
            entry_price=24100.0,
            exit_time=datetime(2024, 1, 2, 10, 15, tzinfo=IST),
            exit_price=24200.0, quantity=25, lot_size=25,
            sl_price=24250.0, target_price=24000.0,
            exit_reason=ExitReason.STOP_LOSS, pnl=-2500.0, outcome=TradeOutcome.LOSS,
            range_high=24200.0, range_low=24050.0,
        ),
        TradeRecord(
            id="t3", strategy="orbs", symbol="BANKNIFTY", exchange="NFO",
            direction=TradeDirection.LONG,
            entry_time=datetime(2024, 1, 2, 10, 0, tzinfo=IST),
            entry_price=45200.0,
            exit_time=datetime(2024, 1, 2, 10, 30, tzinfo=IST),
            exit_price=45500.0, quantity=15, lot_size=15,
            sl_price=45100.0, target_price=45400.0,
            exit_reason=ExitReason.TARGET, pnl=4500.0, outcome=TradeOutcome.WIN,
            range_high=45300.0, range_low=45100.0,
        ),
    ]


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestLearningStatsEndpoint:
    def test_returns_overall_stats(self, client) -> None:
        with patch("engine.api.get_trades_from_db", return_value=_mock_trades()):
            resp = client.get("/api/learning/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "overall" in data
        assert data["overall"]["total_trades"] == 3
        assert data["overall"]["win_rate"] == 66.67

    def test_returns_by_strategy_breakdown(self, client) -> None:
        with patch("engine.api.get_trades_from_db", return_value=_mock_trades()):
            resp = client.get("/api/learning/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "by_strategy" in data
        assert "orbs" in data["by_strategy"]

    def test_returns_by_hour_breakdown(self, client) -> None:
        with patch("engine.api.get_trades_from_db", return_value=_mock_trades()):
            resp = client.get("/api/learning/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "by_hour" in data

    def test_returns_by_day_breakdown(self, client) -> None:
        with patch("engine.api.get_trades_from_db", return_value=_mock_trades()):
            resp = client.get("/api/learning/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "by_day" in data

    def test_empty_db_returns_zeroed_stats(self, client) -> None:
        with patch("engine.api.get_trades_from_db", return_value=[]):
            resp = client.get("/api/learning/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall"]["total_trades"] == 0


class TestParamOptimizeEndpoint:
    def test_accepts_valid_request(self, client) -> None:
        payload = {
            "strategy": "orbs",
            "param_grid": {"target_rr": [1.0, 1.5]},
        }
        with patch("engine.api.get_trades_from_db", return_value=_mock_trades()):
            resp = client.post("/api/learning/optimize", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert len(data["results"]) == 2

    def test_results_ranked(self, client) -> None:
        payload = {
            "strategy": "orbs",
            "param_grid": {"target_rr": [1.0, 1.5]},
        }
        with patch("engine.api.get_trades_from_db", return_value=_mock_trades()):
            resp = client.post("/api/learning/optimize", json=payload)
        data = resp.json()
        scores = [r["score"] for r in data["results"]]
        assert scores == sorted(scores, reverse=True)

    def test_missing_param_grid_returns_422(self, client) -> None:
        resp = client.post("/api/learning/optimize", json={"strategy": "orbs"})
        assert resp.status_code == 422

    def test_unknown_strategy_returns_empty(self, client) -> None:
        payload = {"strategy": "unknown", "param_grid": {"target_rr": [1.0]}}
        with patch("engine.api.get_trades_from_db", return_value=_mock_trades()):
            resp = client.post("/api/learning/optimize", json=payload)
        assert resp.status_code == 200
        assert resp.json()["results"] == []


class TestHealthEndpoint:
    def test_health_returns_ok(self, client) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
