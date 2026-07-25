# mypy: ignore-errors
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from engine.config import EngineConfig
from engine.executor import Executor
from strategies.base import Signal, StrategyBase, TradeDirection
from strategies.config import STRATEGIES

IST = timezone(timedelta(hours=5, minutes=30))

NUM_INSTRUMENTS = sum(len(c.instruments) for c in STRATEGIES.values() if c.enabled)


class _MockStrategy(StrategyBase):
    def __init__(self, name: str, symbol: str, exchange: str, signals: list[Signal] | None = None) -> None:
        super().__init__(name, symbol, exchange)
        self._signals = signals or []

    def generate_signals(self, ohlcv: list[dict]) -> list[Signal]:
        return self._signals

    def run_live(self, ohlcv: list[dict]) -> Signal | None:
        signals = self.generate_signals(ohlcv)
        return signals[-1] if signals else None


@pytest.fixture
def cfg(tmp_path) -> EngineConfig:
    return EngineConfig(
        db_path=tmp_path / "test.db",
        interval_minutes=5,
        dry_run=True,
    )


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.fetch_ohlcv.return_value = []
    client.place_order.return_value = {"status": "dry_run", "order_id": "dry-abc123"}
    client.get_positions.return_value = []
    return client


class TestExecutor:
    def test_run_creates_db(self, cfg, mock_client, tmp_path) -> None:
        ex = Executor(cfg, mock_client)
        assert tmp_path.joinpath("test.db").exists()
        ex.close()

    def test_run_fetches_ohlcv_for_all_instruments(self, cfg, mock_client) -> None:
        with patch("engine.executor.get_strategy") as mock_get:
            mock_get.return_value = _MockStrategy("orbs", "BANKNIFTY", "NFO")
            ex = Executor(cfg, mock_client)
            ex.run()
            assert mock_client.fetch_ohlcv.call_count == NUM_INSTRUMENTS
            ex.close()

    def test_run_generates_signal_and_places_order(self, cfg, mock_client) -> None:
        mock_client.fetch_ohlcv.return_value = [
            {"time": "2024-01-01T09:15:00+05:30", "open": 100, "high": 105, "low": 95, "close": 101, "volume": 1000},
            {"time": "2024-01-01T09:30:00+05:30", "open": 145, "high": 160, "low": 140, "close": 155, "volume": 20000},
        ]
        mock_client.place_order.return_value = {"status": "success", "order_id": "ORD001"}

        def mock_getter(name: str, symbol: str, exchange: str) -> _MockStrategy | None:
            if symbol == "TEST":
                return None
            sig = Signal(
                direction=TradeDirection.LONG,
                entry_price=155,
                sl_price=140,
                target_price=170,
                quantity=15,
                timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=IST),
            )
            return _MockStrategy(name, symbol, exchange, signals=[sig])

        with patch("engine.executor.get_strategy", side_effect=mock_getter):
            ex = Executor(cfg, mock_client)
            ex.run()
            assert mock_client.place_order.call_count == NUM_INSTRUMENTS
            ex.close()

    def test_run_skips_order_when_no_signal(self, cfg, mock_client) -> None:
        with patch("engine.executor.get_strategy") as mock_get:
            mock_get.return_value = _MockStrategy("orbs", "BANKNIFTY", "NFO")
            ex = Executor(cfg, mock_client)
            ex.run()
            mock_client.place_order.assert_not_called()
            ex.close()

    def test_run_handles_error_per_instrument(self, cfg, mock_client) -> None:
        with patch("engine.executor.get_strategy") as mock_get:
            mock_get.side_effect = Exception("Unexpected error")
            ex = Executor(cfg, mock_client)
            ex.run()

    def test_run_skips_disabled_strategies(self, cfg, mock_client) -> None:
        with patch("engine.executor.STRATEGIES", {
            "test_disabled": type("C", (), {"enabled": False, "instruments": []})(),
        }):
            ex = Executor(cfg, mock_client)
            ex.run()
            mock_client.fetch_ohlcv.assert_not_called()
            ex.close()

    def test_run_can_run_multiple_times(self, cfg, mock_client) -> None:
        with patch("engine.executor.get_strategy") as mock_get:
            mock_get.return_value = _MockStrategy("orbs", "BANKNIFTY", "NFO")
            ex = Executor(cfg, mock_client)
            ex.run()
            ex.run()
            assert mock_client.fetch_ohlcv.call_count == NUM_INSTRUMENTS * 2
            ex.close()

    def test_get_recent_trades(self, cfg, mock_client) -> None:
        ex = Executor(cfg, mock_client)
        trades = ex.get_recent_trades()
        assert isinstance(trades, list)
        ex.close()
