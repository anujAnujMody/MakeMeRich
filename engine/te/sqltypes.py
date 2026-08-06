"""SQLAlchemy column types shared by every declaration in the project.

A root-level leaf module rather than `te/persistence/types.py`, because the
layer rule forbids `te.broker` (and `te.data`) from importing
`te.persistence` — and `te.broker.instrument_sync` declares one of the tables
that needs this type. Putting it here is what lets ORM models, the `te.ml`
Core tables and `te.broker`'s Core table all share ONE definition instead of
three.

This module must stay a leaf: it may import `te.domain` and nothing else
from `te`. An import-linter contract enforces that, so the shared type can
never become a back door between tiers.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from te.domain.clock import assume_utc, to_utc


class UtcDateTime(TypeDecorator[dt.datetime]):
    """`DateTime(timezone=True)` that actually round-trips the timezone.

    SQLite has no native timestamp type: SQLAlchemy stores a datetime as the
    ISO string `YYYY-MM-DD HH:MM:SS.ffffff` and **silently discards any UTC
    offset**, so a tz-aware value written through a plain
    `DateTime(timezone=True)` column comes back tz-NAIVE. That is a real
    sqlite3 limitation, not a configuration mistake — `te/domain/clock.py`'s
    `to_utc()`/`assume_utc()` pair was written to work around it by hand.

    Doing it by hand is what failed. Found live on 2026-07-31: every
    timestamp on every dashboard page displayed 5h30m early, because the read
    routers serialised `row.ts.isoformat()` — a naive value — and browsers
    parse an offset-less datetime string as LOCAL time.
    `te/api/routers/trades.py` had already called `assume_utc()` for its
    filter comparisons and still serialised the same column naive twelve
    lines below, which is the clearest possible evidence that "remember to
    call `assume_utc()` at each site" is not a workable invariant.

    So enforce it at the type instead, in both directions:

    * **write** — reject naive input outright (a naive datetime has no
      defined instant, and guessing its zone is how the digits get corrupted
      in the first place), then normalise to UTC so every stored row is on
      one common offset and string ordering matches chronological ordering.
    * **read** — reattach `dt.UTC`, which is sound precisely *because* the
      write side refuses anything it hasn't normalised.

    Both directions DELEGATE to `te.domain.clock`: that pair is the project's
    single definition of this invariant, and a second copy here would be a
    second place to fix.

    **Use this for every timestamp column, in ORM models AND Core tables.**
    The first version of this type lived in `te/persistence/models.py` and so
    covered only the ORM declarations, which left the five Core tables in
    `te.ml`/`te.broker` on the plain type — meaning the docstring's claim
    that naive datetimes "cannot leave the persistence layer" held for some
    tables and not others, with nothing marking which.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect: Dialect) -> dt.datetime | None:
        if value is None:
            return None
        return to_utc(value, name="persisted timestamp")

    def process_result_value(self, value: dt.datetime | None, dialect: Dialect) -> dt.datetime | None:
        if value is None:
            return None
        # Sound only because `process_bind_param` normalised every write to
        # UTC — these digits are known to be UTC, so reattach rather than
        # convert (`astimezone` on a naive value would assume system-local).
        return assume_utc(value)
