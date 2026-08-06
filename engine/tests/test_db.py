"""Tests for te.persistence.db — pragma enforcement on connect."""

from pathlib import Path

from sqlalchemy import text

from te.persistence.db import make_engine, make_session_factory


def test_pragmas_applied(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    engine = make_engine(f"sqlite:///{db_path}")
    session_factory = make_session_factory(engine)

    with session_factory() as session:
        foreign_keys = session.execute(text("PRAGMA foreign_keys")).scalar()
        journal_mode = session.execute(text("PRAGMA journal_mode")).scalar()

    assert foreign_keys == 1
    assert journal_mode == "wal"

    engine.dispose()
