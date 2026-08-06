from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any

from .config import DATA_DIR, RISK


class TradeDirection(Enum):
    LONG = auto()
    SHORT = auto()


class ExitReason(Enum):
    TARGET = auto()
    STOP_LOSS = auto()
    TIME_STOP = auto()
    SIGNAL_REVERSE = auto()
    DAILY_LIMIT = auto()
    MANUAL = auto()


class TradeOutcome(Enum):
    WIN = auto()
    LOSS = auto()
    BREAKEVEN = auto()


@dataclass
class TradeRecord:
    id: str
    strategy: str
    symbol: str
    exchange: str
    direction: TradeDirection
    entry_time: datetime
    entry_price: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    quantity: int = 0
    lot_size: int = 1
    sl_price: float | None = None
    target_price: float | None = None
    exit_reason: ExitReason | None = None
    pnl: float = 0.0
    outcome: TradeOutcome | None = None
    range_high: float = 0.0
    range_low: float = 0.0
    lessons: str = ""


@dataclass
class Signal:
    direction: TradeDirection
    entry_price: float
    sl_price: float
    target_price: float
    quantity: int
    timestamp: datetime


class StrategyBase(ABC):
    def __init__(self, name: str, symbol: str, exchange: str) -> None:
        self.name = name
        self.symbol = symbol
        self.exchange = exchange
        self.trades: list[TradeRecord] = []
        self.daily_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.position: TradeRecord | None = None
        self.db_path: Path = DATA_DIR / "trades.db"

    @abstractmethod
    def generate_signals(self, ohlcv: list[dict[str, Any]]) -> list[Signal]:
        ...

    def calc_position_size(self, price_risk: float, lot_size: int = 1) -> int:
        risk_amount = RISK["max_loss_per_trade"]
        if price_risk <= 0:
            return 0
        risk_per_lot = price_risk * lot_size
        if risk_per_lot <= 0:
            return 0
        lots = int(risk_amount / risk_per_lot)
        if lots < 1:
            return 0
        return lots * lot_size

    def check_risk_gates(self, bar_time: datetime | None = None) -> str | None:
        if abs(self.daily_pnl) >= RISK["max_loss_per_day"]:
            return "daily_loss_limit"
        if self.consecutive_losses >= RISK["max_consecutive_losses"]:
            return "max_consecutive_losses"
        check = bar_time if bar_time else datetime.now(UTC)
        if check.tzinfo:
            ist_local = check.astimezone(timezone(timedelta(hours=5, minutes=30)))
            ist_hour = ist_local.hour + ist_local.minute / 60
        else:
            ist_hour = (check.hour + 5.5) % 24
        if ist_hour >= RISK["no_trade_after_hour"]:
            return "after_trade_hours"
        return None

    def log_trade(self, record: TradeRecord) -> None:
        self.trades.append(record)
        if record.pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        self.daily_pnl += record.pnl

    def run_backtest(self, ohlcv: list[dict[str, Any]]) -> list[TradeRecord]:
        self._reset()
        signals = self.generate_signals(ohlcv)
        for sig in signals:
            gate = self.check_risk_gates(sig.timestamp)
            if gate:
                continue
            record = self._simulate_trade(sig, ohlcv)
            self.log_trade(record)
        return self.trades

    def run_live(self, ohlcv: list[dict[str, Any]]) -> Signal | None:
        gate = self.check_risk_gates()
        if gate:
            return None
        signals = self.generate_signals(ohlcv)
        return signals[-1] if signals else None

    def _reset(self) -> None:
        self.trades.clear()
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.position = None

    def _simulate_trade(self, sig: Signal, ohlcv: list[dict[str, Any]]) -> TradeRecord:
        record = TradeRecord(
            id=f"{self.name}-{self.symbol}-{sig.timestamp.isoformat()}",
            strategy=self.name,
            symbol=self.symbol,
            exchange=self.exchange,
            direction=sig.direction,
            entry_time=sig.timestamp,
            entry_price=sig.entry_price,
            quantity=sig.quantity,
            lot_size=1,
            sl_price=sig.sl_price,
            target_price=sig.target_price,
            range_high=sig.sl_price if sig.direction == TradeDirection.LONG else sig.entry_price,
            range_low=sig.entry_price if sig.direction == TradeDirection.LONG else sig.sl_price,
        )

        entry_idx = self._find_entry_index(sig.timestamp, ohlcv)
        if entry_idx is None:
            return record

        multiplier = 1 if sig.direction == TradeDirection.LONG else -1
        bar_time = sig.timestamp

        for bar in ohlcv[entry_idx:]:
            bar_time = bar.get("time", "")
            high = float(bar.get("high", 0))
            low = float(bar.get("low", 0))

            if sig.direction == TradeDirection.LONG:
                if low <= sig.sl_price:
                    record.exit_price = sig.sl_price
                    record.exit_reason = ExitReason.STOP_LOSS
                elif high >= sig.target_price:
                    record.exit_price = sig.target_price
                    record.exit_reason = ExitReason.TARGET
            else:
                if high >= sig.sl_price:
                    record.exit_price = sig.sl_price
                    record.exit_reason = ExitReason.STOP_LOSS
                elif low <= sig.target_price:
                    record.exit_price = sig.target_price
                    record.exit_reason = ExitReason.TARGET

            if record.exit_price is not None:
                break

        if record.exit_price is None:
            last_bar = ohlcv[-1]
            record.exit_price = float(last_bar.get("close", sig.entry_price))
            record.exit_reason = ExitReason.TIME_STOP

        record.exit_time = datetime.fromisoformat(str(bar_time)) if isinstance(bar_time, str) else sig.timestamp
        record.pnl = round((record.exit_price - sig.entry_price) * sig.quantity * multiplier, 2)
        pnl_pct = record.pnl / (sig.entry_price * sig.quantity) if sig.entry_price > 0 else 0
        if abs(pnl_pct) < 0.001:
            record.outcome = TradeOutcome.BREAKEVEN
        elif record.pnl > 0:
            record.outcome = TradeOutcome.WIN
        else:
            record.outcome = TradeOutcome.LOSS

        return record

    @staticmethod
    def _find_entry_index(timestamp: datetime, ohlcv: list[dict[str, Any]]) -> int | None:
        for i, bar in enumerate(ohlcv):
            bar_time = bar.get("time", "")
            if isinstance(bar_time, str):
                try:
                    bt = datetime.fromisoformat(bar_time)
                    if bt >= timestamp:
                        return i
                except (ValueError, TypeError):
                    continue
        return None
