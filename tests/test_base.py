from datetime import datetime, timedelta, timezone
from typing import Any

from strategies.base import (
    ExitReason,
    Signal,
    StrategyBase,
    TradeDirection,
    TradeOutcome,
)
from strategies.config import RISK

IST = timezone(timedelta(hours=5, minutes=30))


class _TestStrategy(StrategyBase):
    def generate_signals(self, ohlcv: list[dict[str, Any]]) -> list[Signal]:
        return []


class TestCalcPositionSize:
    def test_normal(self) -> None:
        s = _TestStrategy("test", "SBIN", "NSE")
        qty = s.calc_position_size(price_risk=5, lot_size=15)
        assert qty == 30

    def test_exceeds_risk(self) -> None:
        s = _TestStrategy("test", "SBIN", "NSE")
        qty = s.calc_position_size(price_risk=200, lot_size=15)
        assert qty == 0

    def test_zero_risk(self) -> None:
        s = _TestStrategy("test", "SBIN", "NSE")
        assert s.calc_position_size(price_risk=0, lot_size=1) == 0


class TestCheckRiskGates:
    def test_daily_loss_limit_blocks(self) -> None:
        s = _TestStrategy("test", "SBIN", "NSE")
        s.daily_pnl = -RISK["max_loss_per_day"]
        assert s.check_risk_gates() == "daily_loss_limit"

    def test_consecutive_losses_blocks(self) -> None:
        s = _TestStrategy("test", "SBIN", "NSE")
        s.consecutive_losses = RISK["max_consecutive_losses"]
        assert s.check_risk_gates() == "max_consecutive_losses"

    def test_after_trade_hours_blocks(self) -> None:
        s = _TestStrategy("test", "SBIN", "NSE")
        bar_time = datetime(2024, 1, 1, 11, 30, tzinfo=IST)
        assert s.check_risk_gates(bar_time) == "after_trade_hours"


class TestSimulateTrade:
    def test_long_hits_target(self) -> None:
        s = _TestStrategy("test", "SBIN", "NSE")
        sig = Signal(
            direction=TradeDirection.LONG,
            entry_price=100,
            sl_price=90,
            target_price=120,
            quantity=10,
            timestamp=datetime(2024, 1, 1, 10, 0, tzinfo=IST),
        )
        ohlcv: list[dict[str, Any]] = [
            {"time": "2024-01-01T10:05:00+05:30", "open": 101, "high": 105, "low": 100, "close": 104, "volume": 1000},
            {"time": "2024-01-01T10:10:00+05:30", "open": 105, "high": 125, "low": 103, "close": 122, "volume": 2000},
        ]
        record = s._simulate_trade(sig, ohlcv)
        assert record.exit_price == 120
        assert record.exit_reason == ExitReason.TARGET
        assert record.pnl == 200.0
        assert record.outcome == TradeOutcome.WIN
