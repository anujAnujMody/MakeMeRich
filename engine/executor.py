from engine.config import EngineConfig
from engine.db import get_trades, init_db, persist_trade
from engine.openalgo_client import OpenAlgoClient
from strategies.base import TradeRecord
from strategies.config import STRATEGIES, get_instruments
from strategies.registry import get_strategy


class Executor:
    def __init__(self, config: EngineConfig, client: OpenAlgoClient | None = None) -> None:
        self.config = config
        self.client = client or OpenAlgoClient(host=config.openalgo_host, dry_run=config.dry_run)
        self.conn = init_db(str(config.db_path))

    def run(self) -> None:
        for name, strategy_cfg in STRATEGIES.items():
            if not strategy_cfg.enabled:
                continue
            instruments = get_instruments(name)
            for instrument in instruments:
                try:
                    self._process_instrument(name, instrument.symbol, instrument.exchange)
                except Exception as e:  # noqa: BLE001
                    print(f"Error processing {name}/{instrument.symbol}: {e}")

    def _process_instrument(self, strategy_name: str, symbol: str, exchange: str) -> None:
        ohlcv = self.client.fetch_ohlcv(symbol, exchange)
        if not ohlcv:
            return

        strategy = get_strategy(strategy_name, symbol, exchange)
        if strategy is None:
            return

        signal = strategy.run_live(ohlcv)
        if signal is None:
            return

        result = self.client.place_order(
            symbol=symbol,
            exchange=exchange,
            direction="BUY" if signal.direction.name == "LONG" else "SELL",
            quantity=signal.quantity,
            order_type="MARKET",
            price=signal.entry_price,
        )

        record = TradeRecord(
            id=result.get("order_id", f"unknown-{symbol}"),
            strategy=strategy_name,
            symbol=symbol,
            exchange=exchange,
            direction=signal.direction,
            entry_time=signal.timestamp,
            entry_price=signal.entry_price,
            quantity=signal.quantity,
            lot_size=1,
            sl_price=signal.sl_price,
            target_price=signal.target_price,
        )
        persist_trade(self.conn, record)

    def get_recent_trades(self, limit: int = 20) -> list[TradeRecord]:
        return get_trades(self.conn, limit=limit)

    def close(self) -> None:
        self.conn.close()
