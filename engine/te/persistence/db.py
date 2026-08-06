"""SQLAlchemy 2 engine/session setup.

SQLite footguns are off by default (`foreign_keys` is OFF unless a session
explicitly turns it on), so every connection gets a `connect` hook that sets
WAL journaling, `NORMAL` synchronous durability, a busy timeout so concurrent
writers back off instead of raising immediately, and `foreign_keys=ON`.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from te.settings import Settings

_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=5000",
    "PRAGMA foreign_keys=ON",
)


def _apply_pragmas(dbapi_connection: object, _connection_record: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    for pragma in _PRAGMAS:
        cursor.execute(pragma)
    cursor.close()


def make_engine(database_url: str) -> Engine:
    """Builds the SQLAlchemy engine, wiring the pragma hook onto every new
    DBAPI connection (SQLite pools open a fresh connection per checkout).

    `:memory:` URLs get a `StaticPool` + `check_same_thread=False` so the
    single in-process connection is shared across threads (FastAPI's
    request handlers run in a worker thread pool) — without this, each
    checkout would open a BRAND NEW, empty in-memory database and every
    query would fail with "no such table". The plan's own rule is that real
    tests use a per-test temp FILE db, never `:memory:`, precisely to avoid
    this kind of connection-identity footgun; this branch exists only so
    `:memory:` (used as `TE_DATABASE_URL`'s test-harness fallback, see
    `tests/conftest.py`) behaves correctly for callers that do share an
    engine across threads, e.g. `te.api.db`."""
    is_memory = ":memory:" in database_url
    engine = (
        create_engine(database_url, future=True, poolclass=StaticPool, connect_args={"check_same_thread": False})
        if is_memory
        else create_engine(database_url, future=True)
    )
    event.listen(engine, "connect", _apply_pragmas)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def engine_from_settings(settings: Settings) -> Engine:
    return make_engine(settings.database_url)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commits on clean exit, rolls back on exception."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
