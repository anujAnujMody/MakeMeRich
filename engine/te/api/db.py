"""Shared module-singletons for API routers backed by real persisted/recorded
data. Built once at import time from `Settings`. Was previously just the DB
session-factory (Phase 4: `mode`, `approvals`); `bar_store`/`charge_rate_table`
were added here after 3 routers (`dashboard`, `positions`, `engine`) had each
independently constructed their own identical copies of all three.

`Base.metadata.create_all` is called defensively so these tables exist even
when Alembic migrations haven't been run yet (e.g. under tests, which run
against `TE_DATABASE_URL=sqlite:///:memory:` per `tests/conftest.py`) — in
normal operation the tables already exist via migration and this is a
no-op (`checkfirst=True` is `create_all`'s default).
"""

from __future__ import annotations

from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.persistence.db import engine_from_settings, make_session_factory
from te.persistence.models import Base
from te.settings import Settings

settings = Settings()
engine = engine_from_settings(settings)
Base.metadata.create_all(engine)
session_factory = make_session_factory(engine)
bar_store = BarStore(settings.bar_store_path)
charge_rate_table = load_charge_rate_table(settings.charges_path)
