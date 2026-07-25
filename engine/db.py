import sqlite3
from datetime import datetime
from typing import Any

from strategies.base import (
    ExitReason,
    TradeDirection,
    TradeOutcome,
    TradeRecord,
)


def _row_to_record(row: tuple[Any, ...]) -> TradeRecord:
    def _parse_dt(val: str | None) -> datetime | None:
        if val:
            try:
                return datetime.fromisoformat(val)
            except (ValueError, TypeError):
                return None
        return None

    return TradeRecord(
        id=row[0],
        strategy=row[1],
        symbol=row[2],
        exchange=row[3],
        direction=TradeDirection[row[4]],
        entry_time=datetime.fromisoformat(row[5]),
        entry_price=row[6],
        exit_time=_parse_dt(row[7]),
        exit_price=row[8],
        quantity=row[9],
        lot_size=1,
        sl_price=row[10],
        target_price=row[11],
        exit_reason=ExitReason[row[12]] if row[12] else None,
        pnl=row[13] or 0.0,
        outcome=TradeOutcome[row[14]] if row[14] else None,
        range_high=row[15] or 0.0,
        range_low=row[16] or 0.0,
        lessons=row[17] or "",
    )


_TABLE_SQL = """
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
"""


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(_TABLE_SQL)
    conn.commit()
    return conn


def persist_trade(conn: sqlite3.Connection, record: TradeRecord) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO trades
        (id, strategy, symbol, exchange, direction, entry_time, entry_price,
         exit_time, exit_price, quantity, sl_price, target_price,
         exit_reason, pnl, outcome, range_high, range_low, lessons)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.id,
            record.strategy,
            record.symbol,
            record.exchange,
            record.direction.name,
            record.entry_time.isoformat() if record.entry_time else "",
            record.entry_price,
            record.exit_time.isoformat() if record.exit_time else None,
            record.exit_price,
            record.quantity,
            record.sl_price,
            record.target_price,
            record.exit_reason.name if record.exit_reason else None,
            record.pnl,
            record.outcome.name if record.outcome else None,
            record.range_high,
            record.range_low,
            record.lessons,
        ),
    )
    conn.commit()


def get_trades(
    conn: sqlite3.Connection,
    strategy: str | None = None,
    symbol: str | None = None,
    limit: int = 100,
) -> list[TradeRecord]:
    query = "SELECT * FROM trades WHERE 1=1"
    params: list[str] = []
    if strategy:
        query += " AND strategy = ?"
        params.append(strategy)
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)
    query += " ORDER BY entry_time DESC LIMIT ?"
    params.append(str(limit))
    rows = conn.execute(query, params).fetchall()
    return [_row_to_record(r) for r in rows]
