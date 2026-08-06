"""Every timestamp the API emits must carry an explicit UTC offset.

Found live on 2026-07-31: the dashboard's skipped-signal feed showed
`07:10:00` for a cycle that ran at `12:40 IST`. Nothing was wrong with the
engine's clock — the *serialisation* dropped the timezone.

The mechanism, end to end:

1. `to_utc()` normalises every write to UTC (`te/domain/clock.py:47`).
2. SQLite's `DateTime(timezone=True)` cannot store an offset, so the value
   comes back tz-NAIVE on read — a real sqlite3/SQLAlchemy limitation, and
   one `te/domain/clock.py:65`'s `assume_utc()` exists specifically to undo.
3. The read routers called `row.ts.isoformat()` on that naive value, so the
   JSON carried `"2026-07-31T07:15:00"` with NO offset.
4. ECMAScript parses a date-time string with no offset as LOCAL time, so
   `new Date(...)` in the browser read 07:15 UTC as 07:15 IST.

Net effect: every timestamp on every page was 5h30m early. `trades.py` is
the sharpest illustration of why a per-call-site fix was not enough — it
already called `assume_utc()` for its `from`/`to` FILTER comparisons (line
41) while serialising the same column naive twelve lines later.

The fix is therefore structural, at the column type (`UtcDateTime` in
`te/persistence/models.py`), not at the eight call sites: a
`result_processor` that reattaches `dt.UTC` on every read makes a naive
datetime unable to escape the persistence layer at all. These tests pin the
observable behaviour so the bug class cannot silently return.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import te.api.routers.decisions as decisions_router
import te.api.routers.execution as execution_router
import te.api.routers.trades as trades_router
from te.domain.clock import IST
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, CycleEvaluationRow, TradeRow
from te.persistence.repos.paper_trading import record_skipped_signal


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from fastapi import FastAPI

    engine = make_engine(f"sqlite+pysqlite:///{tmp_path / 'tz.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    for module in (decisions_router, execution_router, trades_router):
        monkeypatch.setattr(module, "session_factory", factory)

    # 12:40:00 IST == 07:10:00 UTC — the exact pair of wall-clock readings
    # from the screenshot that exposed this. The TIME is the fixture; the
    # DATE must be today's, because `/api/decisions/today` filters to the
    # current IST date. Pinned to a literal 2026-07-31 originally, this
    # whole file started failing the moment the clock passed midnight into
    # 2026-08-01 — a fixture that expires is indistinguishable from a
    # regression at the moment you most want to trust the suite.
    ist_now = dt.datetime.combine(dt.datetime.now(IST).date(), dt.time(12, 40), tzinfo=IST)
    with factory() as session:
        record_skipped_signal(session, ts=ist_now, strategy="orb", instrument="NIFTY", reason="no breakout")
        session.add(
            CycleEvaluationRow(
                cycle_id=1,
                evaluation_id="orb-NIFTY-1",
                ts=ist_now.astimezone(dt.UTC),
                strategy="orb",
                instrument="NIFTY",
                verdict="skipped",
                reason="no breakout",
            )
        )
        session.add(
            TradeRow(
                client_order_id="c1",
                symbol="NIFTY31JUL2624350CE",
                exchange="NFO",
                strategy="orb",
                direction="long_call",
                lots=1,
                lot_size=65,
                entry_premium_paise=9_600,
                exit_premium_paise=10_200,
                gross_pnl_paise=39_000,
                costs_paise=6_200,
                net_pnl_paise=32_800,
                exit_reason="target",
                opened_at=ist_now.astimezone(dt.UTC),
                closed_at=ist_now.astimezone(dt.UTC),
            )
        )
        session.commit()

    app = FastAPI()
    for module in (decisions_router, execution_router, trades_router):
        app.include_router(module.router)
    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ("/api/execution/skipped", "timestamp"),
        ("/api/decisions/today", "timestamp"),
        ("/api/trades", "timestamp"),
    ],
)
def test_emitted_timestamps_carry_an_explicit_utc_offset(client: TestClient, path: str, field: str) -> None:
    """A bare `2026-07-31T07:10:00` is parsed as LOCAL time by every browser,
    which is precisely how a 07:10 UTC cycle rendered as 07:10 IST. Requiring
    a parsed offset — rather than string-matching a `Z`/`+00:00` suffix —
    keeps the assertion about the semantics, not the spelling."""
    rows = client.get(path).json()
    assert rows, f"{path} returned no rows — the fixture seeding regressed, not the timezone behaviour"

    parsed = dt.datetime.fromisoformat(rows[0][field])
    assert parsed.tzinfo is not None, (
        f"{path}.{field} == {rows[0][field]!r} has no UTC offset; a browser will read it as local time "
        f"and display it {IST.utcoffset(dt.datetime.now()) or ''} off"
    )


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ("/api/execution/skipped", "timestamp"),
        ("/api/decisions/today", "timestamp"),
        ("/api/trades", "timestamp"),
    ],
)
def test_emitted_timestamps_round_trip_to_the_correct_ist_wall_clock(
    client: TestClient, path: str, field: str
) -> None:
    """The value must not merely be offset-qualified, it must be the RIGHT
    instant: converting back to IST has to reproduce the 12:40 the operator
    saw on the wall, not the 07:10 the dashboard was showing."""
    rows = client.get(path).json()
    as_ist = dt.datetime.fromisoformat(rows[0][field]).astimezone(IST)

    assert (as_ist.hour, as_ist.minute) == (12, 40), (
        f"{path}.{field} == {rows[0][field]!r} converts to {as_ist:%H:%M} IST, expected 12:40 IST"
    )
