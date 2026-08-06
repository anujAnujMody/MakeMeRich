"""Shared fetch-headers + ingest wrapper for the NSE/BSE bhavcopy modules.

`bhavcopy_nse.py` and `bhavcopy_bse.py` differ only in their URL, their
`SOURCE` constant, and which fetch/parse pair they close over — the
try/fetch/parse/`record_ingest`/re-raise shape around them is identical, so
it lives here once. Kept out of `bhav_types.py` so that module stays a pure
dataclass module with no `sqlalchemy` dependency.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

import sqlalchemy as sa

from te.data.bhav_store import write_option_bhav_rows
from te.data.bhav_types import OptionBhavRow
from te.data.ingest_log import record_ingest

#: NSE serves its archive to a browser-shaped request; a bare `httpx.get`
#: without a User-Agent is commonly rejected with a 403 by NSE's edge
#: (well-known NSE quirk, not specific to one endpoint). BSE is fetched with
#: the same headers.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


def ingest(
    engine: sa.Engine,
    trade_date: dt.date,
    *,
    source: str,
    fetch: Callable[[dt.date], str],
    parse: Callable[[str], list[OptionBhavRow]],
) -> list[OptionBhavRow]:
    """Fetches, parses, PERSISTS (via `write_option_bhav_rows` — see that
    module's docstring for why this step was missing until 2026-07-30), and
    records an ingest-log row for one trading day's bhavcopy. Returns the
    parsed rows. Any fetch/parse error writes a "failed" log row (row_count
    0, exception text as detail) and is then RE-RAISED — never swallowed
    into a silently-empty ingest."""
    try:
        rows = parse(fetch(trade_date))
    except Exception as exc:
        record_ingest(engine, source=source, trade_date=trade_date, row_count=0, status="failed", detail=str(exc))
        raise

    write_option_bhav_rows(engine, rows)
    record_ingest(engine, source=source, trade_date=trade_date, row_count=len(rows), status="success")
    return rows
