import argparse
import signal
import sqlite3
import sys
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast

from ..base import Signal, StrategyBase, TradeDirection, TradeOutcome, TradeRecord
from ..config import STRATEGIES
from ..registry import register


def _ist_now() -> datetime:
    return datetime.now(UTC) + timedelta(hours=5, minutes=30)


def _ist_time(hour: int, minute: int = 0) -> datetime:
    now = _ist_now()
    return datetime(
        now.year, now.month, now.day, hour, minute, tzinfo=timezone(timedelta(hours=5, minutes=30))
    )


@register("orbs")
class ORBStrategy(StrategyBase):
    def __init__(self, name: str, symbol: str, exchange: str) -> None:
        super().__init__(name, symbol, exchange)
        cfg = STRATEGIES.get(name)
        self.range_min = 15
        self.target_rr = 1.5
        self.sl_rr = 1.0
        self.time_exit_hour = 15
        self.time_exit_minute = 15
        self.min_range_pts = 20
        self.max_range_pts = 200
        self.volume_multiplier = 1.5
        self.max_per_direction = 1
        self.lot_size = 1
        if cfg:
            for inst in cfg.instruments:
                if inst.symbol == symbol:
                    self.range_min = inst.range_min
                    self.lot_size = inst.lot_size
                    break
            self.target_rr = cfg.params.target_rr
            self.sl_rr = cfg.params.sl_rr
            self.time_exit_hour = cfg.params.time_exit_hour
            self.time_exit_minute = cfg.params.time_exit_minute
            self.min_range_pts = cfg.params.min_range_pts
            self.max_range_pts = cfg.params.max_range_pts
            self.volume_multiplier = cfg.params.volume_multiplier
            self.max_per_direction = cfg.params.max_per_direction

        self._range_high: float = 0.0
        self._range_low: float = 0.0
        self._range_width: float = 0.0
        self._avg_range_volume: float = 0.0
        self.trades_today: dict[str, int] = {"LONG": 0, "SHORT": 0}

    def generate_signals(self, ohlcv: list[dict[str, Any]]) -> list[Signal]:
        if not ohlcv:
            return []

        range_end = self._range_end_time(ohlcv)
        if range_end is None:
            return []

        range_candles: list[dict[str, Any]] = []
        post_range: list[dict[str, Any]] = []

        for bar in ohlcv:
            bar_time = self._parse_time(bar.get("time", ""))
            if bar_time is None:
                continue
            if bar_time < range_end:
                range_candles.append(bar)
            else:
                post_range.append(bar)

        if not range_candles:
            return []

        self._range_high = max(float(b.get("high", 0)) for b in range_candles)
        self._range_low = min(float(b.get("low", 0)) for b in range_candles)
        self._range_width = self._range_high - self._range_low

        if self._range_width <= 0:
            return []

        if self._range_width < self.min_range_pts or self._range_width > self.max_range_pts:
            return []

        vols = [float(b.get("volume", 0)) for b in range_candles]
        self._avg_range_volume = sum(vols) / len(vols) if vols else 0
        min_volume = self._avg_range_volume * self.volume_multiplier

        for bar in post_range:
            close = float(bar.get("close", 0))
            volume = float(bar.get("volume", 0))
            bar_time = self._parse_time(bar.get("time", ""))

            if close > self._range_high and volume >= min_volume:
                if self.trades_today["LONG"] >= self.max_per_direction:
                    continue
                sl = round(self._range_low, 2)
                target = round(self._range_high + self._range_width * self.target_rr, 2)
                price_risk = abs(self._range_high - sl)
                qty = self.calc_position_size(price_risk, self.lot_size)
                if qty <= 0:
                    return []
                self.trades_today["LONG"] += 1
                return [
                    Signal(
                        direction=TradeDirection.LONG,
                        entry_price=close,
                        sl_price=sl,
                        target_price=target,
                        quantity=qty,
                        timestamp=bar_time or _ist_now(),
                    )
                ]

            elif close < self._range_low and volume >= min_volume:
                if self.trades_today["SHORT"] >= self.max_per_direction:
                    continue
                sl = round(self._range_high, 2)
                target = round(self._range_low - self._range_width * self.target_rr, 2)
                price_risk = abs(self._range_low - sl)
                qty = self.calc_position_size(price_risk, self.lot_size)
                if qty <= 0:
                    return []
                self.trades_today["SHORT"] += 1
                return [
                    Signal(
                        direction=TradeDirection.SHORT,
                        entry_price=close,
                        sl_price=sl,
                        target_price=target,
                        quantity=qty,
                        timestamp=bar_time or _ist_now(),
                    )
                ]

        return []

    def _range_end_time(self, ohlcv: list[dict[str, Any]]) -> datetime | None:
        for bar in ohlcv:
            bt = self._parse_time(bar.get("time", ""))
            if bt is not None:
                return bt + timedelta(minutes=self.range_min)
        return None

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except (ValueError, TypeError):
                return None
        return None


def load_ohlcv_from_yf(symbol: str, days: int = 5) -> list[dict[str, Any]]:
    try:
        import yfinance as yf  # type: ignore[import-untyped]
    except ImportError:
        print("yfinance not installed. Run: pip install yfinance")
        sys.exit(1)

    ticker = yf.Ticker(symbol + ".NS" if symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY"] else symbol)
    df = ticker.history(period=f"{days}d", interval="5m")
    if df.empty:
        print(f"No data for {symbol}")
        return []

    bars: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        bars.append({
            "time": idx.isoformat(),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        })
    return bars


def load_ohlcv_from_openalgo(symbol: str, exchange: str) -> list[dict[str, Any]]:
    import json
    import os
    import urllib.request

    host = os.environ.get("HOST_SERVER") or os.environ.get("OPENALGO_HOST", "http://localhost:5000")
    url = f"{host}/api/history?symbol={symbol}&exchange={exchange}&interval=5m"

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if isinstance(data, list):
                return cast("list[dict[str, Any]]", data)
            raw = data.get("data", [])
            return cast("list[dict[str, Any]]", raw) if isinstance(raw, list) else []
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"Failed to fetch data from OpenAlgo: {e}")
        return []


def setup_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            strategy TEXT NOT NULL,
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_time TEXT,
            exit_price REAL,
            quantity INTEGER NOT NULL,
            sl_price REAL,
            target_price REAL,
            exit_reason TEXT,
            pnl REAL,
            outcome TEXT,
            range_high REAL,
            range_low REAL,
            lessons TEXT
        )
    """)
    conn.commit()
    return conn


def persist_trades(conn: sqlite3.Connection, trades: list[TradeRecord]) -> None:
    for t in trades:
        conn.execute(
            """
            INSERT OR REPLACE INTO trades
            (id, strategy, symbol, exchange, direction, entry_time, entry_price,
             exit_time, exit_price, quantity, sl_price, target_price,
             exit_reason, pnl, outcome, range_high, range_low, lessons)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                t.id,
                t.strategy,
                t.symbol,
                t.exchange,
                t.direction.name,
                t.entry_time.isoformat() if t.entry_time else "",
                t.entry_price,
                t.exit_time.isoformat() if t.exit_time else None,
                t.exit_price,
                t.quantity,
                t.sl_price,
                t.target_price,
                t.exit_reason.name if t.exit_reason else None,
                t.pnl,
                t.outcome.name if t.outcome else None,
                t.range_high,
                t.range_low,
                t.lessons,
            ),
        )
    conn.commit()


def print_summary(trades: list[TradeRecord]) -> None:
    if not trades:
        print("No trades generated.")
        return

    wins = [t for t in trades if t.outcome == TradeOutcome.WIN]
    losses = [t for t in trades if t.outcome == TradeOutcome.LOSS]
    total_pnl = sum(t.pnl for t in trades)
    win_rate = len(wins) / len(trades) * 100 if trades else 0

    print(f"\n{'='*50}")
    print(f"ORB Backtest: {trades[0].symbol}")
    print(f"{'='*50}")
    print(f"Total trades:  {len(trades)}")
    print(f"Wins:          {len(wins)}")
    print(f"Losses:        {len(losses)}")
    print(f"Win rate:      {win_rate:.1f}%")
    print(f"Total P&L:     Rs {total_pnl:.2f}")
    print(f"Avg win:       Rs {(sum(t.pnl for t in wins) / len(wins)) if wins else 0:.2f}")
    print(f"Avg loss:      Rs {(sum(t.pnl for t in losses) / len(losses)) if losses else 0:.2f}")
    print(f"{'='*50}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ORB Strategy - Dual Mode")
    parser.add_argument("--mode", choices=["backtest", "live"], default=None,
                        help="Run mode (default: env MODE or 'backtest')")
    parser.add_argument("--symbol", default=None, help="Trading symbol (default from config)")
    parser.add_argument("--exchange", default=None, help="Exchange (default from config)")
    return parser.parse_args()


def main() -> None:
    import os

    args = parse_args()
    mode = args.mode or os.environ.get("MODE", "backtest")

    symbol = args.symbol or os.environ.get("SYMBOL") or "BANKNIFTY"
    exchange = args.exchange or os.environ.get("EXCHANGE") or "NFO"

    strategy = ORBStrategy("orbs", symbol, exchange)

    def _shutdown(sig: int, frame: Any) -> None:
        print(f"Signal {sig} received, shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    if mode == "backtest":
        print(f"Loading OHLCV data for {symbol}...")
        ohlcv = load_ohlcv_from_yf(symbol)
        if not ohlcv:
            print("No data available for backtest.")
            sys.exit(1)
        print(f"Loaded {len(ohlcv)} bars. Running backtest...")
        trades = strategy.run_backtest(ohlcv)
        print_summary(trades)

        db_path = strategy.db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = setup_db(str(db_path))
        persist_trades(conn, trades)
        conn.close()
        print(f"Trades saved to {db_path}")

    else:
        print(f"Starting live mode for {symbol}:{exchange}")
        ohlcv = load_ohlcv_from_openalgo(symbol, exchange)
        if not ohlcv:
            print("No live data. Running in paper mode with simulated data.")
        signal_result = strategy.run_live(ohlcv)
        if signal_result:
            print(f"Signal generated: {signal_result.direction.name} at {signal_result.entry_price}")
        else:
            print("No signal. Risk gates may be active, check risk rules.")


if __name__ == "__main__":
    main()
