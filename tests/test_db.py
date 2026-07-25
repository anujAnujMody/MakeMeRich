# mypy: ignore-errors
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine.db import get_trades, init_db, persist_trade
from strategies.base import ExitReason, TradeDirection, TradeOutcome, TradeRecord

IST = timezone(timedelta(hours=5, minutes=30))


class TestInitDb:
    def test_creates_tables(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        conn = init_db(db)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = [r[0] for r in tables]
        assert "trades" in names
        conn.close()

    def test_idempotent(        self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        conn1 = init_db(db)
        conn2 = init_db(db)
        assert conn1 is not None
        assert conn2 is not None
        conn1.close()
        conn2.close()


class TestPersistTrade:
    def _make_record(self) -> TradeRecord:
        return TradeRecord(
            id="test-001",
            strategy="orbs",
            symbol="BANKNIFTY",
            exchange="NFO",
            direction=TradeDirection.LONG,
            entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=IST),
            entry_price=45000.0,
            exit_time=datetime(2024, 1, 1, 10, 30, tzinfo=IST),
            exit_price=45200.0,
            quantity=15,
            lot_size=15,
            sl_price=44900.0,
            target_price=45300.0,
            exit_reason=ExitReason.TARGET,
            pnl=3000.0,
            outcome=TradeOutcome.WIN,
            range_high=45100.0,
            range_low=44900.0,
            lessons="",
        )

    def test_insert_and_query(        self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        conn = init_db(db)
        record = self._make_record()
        persist_trade(conn, record)
        rows = conn.execute("SELECT * FROM trades").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "test-001"
        conn.close()

    def test_insert_twice_replaces(        self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        conn = init_db(db)
        record = self._make_record()
        persist_trade(conn, record)
        persist_trade(conn, record)
        rows = conn.execute("SELECT * FROM trades").fetchall()
        assert len(rows) == 1
        conn.close()

    def test_multiple_trades(        self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        conn = init_db(db)
        r1 = self._make_record()
        r2 = self._make_record()
        r2.id = "test-002"
        persist_trade(conn, r1)
        persist_trade(conn, r2)
        rows = conn.execute("SELECT * FROM trades").fetchall()
        assert len(rows) == 2
        conn.close()


class TestGetTrades:
    def test_get_all(        self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        conn = init_db(db)
        r = TradeRecord(
            id="get-001",
            strategy="orbs",
            symbol="BANKNIFTY",
            exchange="NFO",
            direction=TradeDirection.LONG,
            entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=IST),
            entry_price=45000.0,
            quantity=15,
            lot_size=15,
        )
        persist_trade(conn, r)
        trades = get_trades(conn)
        assert len(trades) == 1
        assert trades[0].id == "get-001"
        conn.close()

    def test_get_empty(        self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        conn = init_db(db)
        trades = get_trades(conn)
        assert trades == []
        conn.close()

    def test_get_by_strategy(        self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        conn = init_db(db)
        r1 = TradeRecord(
            id="str-001", strategy="orbs", symbol="BANKNIFTY", exchange="NFO",
            direction=TradeDirection.LONG, entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=IST),
            entry_price=45000.0, quantity=15, lot_size=15,
        )
        r2 = TradeRecord(
            id="str-002", strategy="test", symbol="NIFTY", exchange="NFO",
            direction=TradeDirection.SHORT, entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=IST),
            entry_price=20000.0, quantity=25, lot_size=25,
        )
        persist_trade(conn, r1)
        persist_trade(conn, r2)
        trades = get_trades(conn, strategy="orbs")
        assert len(trades) == 1
        assert trades[0].id == "str-001"
        conn.close()

    def test_get_by_symbol(        self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        conn = init_db(db)
        r = TradeRecord(
            id="sym-001", strategy="orbs", symbol="FINNIFTY", exchange="NFO",
            direction=TradeDirection.LONG, entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=IST),
            entry_price=20000.0, quantity=25, lot_size=25,
        )
        persist_trade(conn, r)
        trades = get_trades(conn, symbol="FINNIFTY")
        assert len(trades) == 1
        trades = get_trades(conn, symbol="BANKNIFTY")
        assert trades == []
        conn.close()
