"""Pulls lot sizes / expiry rules from the broker (via OpenAlgo) into an
`instruments` table.

This is what makes lot sizes non-hardcoded, per the plan's repeated
emphasis: lot sizes change roughly every 6 months (NIFTY 75->65,
BANKNIFTY 35->30, SENSEX 10->20, all in the Jan 2026 revision) and must
never appear as literals in code — every consumer reads the current row
from this table instead.

Declared with a private `sqlalchemy.Core` `Table` (matching
`te/data/ingest_log.py`'s pattern) so `te/broker/*` stays inside the
"data/broker subpackages may only import domain" layer rule — the caller
supplies a plain `sqlalchemy.Engine`, not a `te.persistence` session.
`te/persistence/models.py` declares the matching ORM model (`Instrument`)
for read-side use by later phases/repos.
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa

from te.broker.openalgo_rest import OpenAlgoRestClient, SymbolMeta

metadata = sa.MetaData()

instruments = sa.Table(
    "instruments",
    metadata,
    sa.Column("symbol", sa.String(64), primary_key=True),
    sa.Column("exchange", sa.String(16), primary_key=True),
    sa.Column("name", sa.String(32), nullable=False),  # underlying, e.g. "NIFTY"
    sa.Column("instrument_type", sa.String(8), nullable=False),  # "FUT" | "CE" | "PE"
    sa.Column("expiry", sa.String(16), nullable=False),
    sa.Column("strike", sa.Float, nullable=False),
    sa.Column("lot_size", sa.Integer, nullable=False),
    sa.Column("tick_size", sa.Float, nullable=False),
    sa.Column("source", sa.String(32), nullable=False, default="openalgo"),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

#: The `instruments.source` value written by this module — every row here
#: came from OpenAlgo's symbol service by construction, so it is a constant
#: at the write site rather than a field carried on every fetched row.
_SOURCE = "openalgo"


def fetch_instruments(client: OpenAlgoRestClient, contracts: list[tuple[str, str]]) -> list[SymbolMeta]:
    """Resolves lot size / tick size / expiry for each `(symbol, exchange)`
    pair via OpenAlgo's `symbol` service."""
    return [client.symbol_meta(symbol, exchange) for symbol, exchange in contracts]


def sync_instruments(engine: sa.Engine, rows: list[SymbolMeta]) -> int:
    """Upserts `rows` into the `instruments` table, keyed on
    `(symbol, exchange)`. Returns the number of rows written."""
    metadata.create_all(engine, checkfirst=True)
    if not rows:
        return 0

    now = dt.datetime.now(dt.UTC)
    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                sa.delete(instruments).where(
                    instruments.c.symbol == row.symbol,
                    instruments.c.exchange == row.exchange,
                )
            )
            conn.execute(
                instruments.insert().values(
                    symbol=row.symbol,
                    exchange=row.exchange,
                    name=row.name,
                    instrument_type=row.instrument_type,
                    expiry=row.expiry,
                    strike=row.strike,
                    lot_size=row.lot_size,
                    tick_size=row.tick_size,
                    source=_SOURCE,
                    updated_at=now,
                )
            )
    return len(rows)
