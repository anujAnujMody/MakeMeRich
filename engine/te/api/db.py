"""Shared DB session-factory for API routers backed by real persisted data
(Phase 4: `mode`, `approvals`). Built once at import time from `Settings`,
mirroring `te.api.state`'s existing module-singleton pattern.

`Base.metadata.create_all` is called defensively so these tables exist even
when Alembic migrations haven't been run yet (e.g. under tests, which run
against `TE_DATABASE_URL=sqlite:///:memory:` per `tests/conftest.py`) — in
normal operation the tables already exist via migration and this is a
no-op (`checkfirst=True` is `create_all`'s default).
"""

from __future__ import annotations

from te.persistence.db import engine_from_settings, make_session_factory
from te.persistence.models import Base
from te.settings import Settings

_settings = Settings()
engine = engine_from_settings(_settings)
Base.metadata.create_all(engine)
session_factory = make_session_factory(engine)
