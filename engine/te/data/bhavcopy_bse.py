"""BSE derivatives daily bhavcopy ingestion — SENSEX/BANKEX index options
(NSE does not list these; SENSEX/BANKEX only trade on BSE).

**Fetch URL is unverified against a live download in this sandbox** (no
network egress — see the accompanying report, and `bhavcopy_nse.py`'s
module docstring for the same caveat). BSE adopted the same SEBI-mandated
UDiFF column layout as NSE for its derivatives bhavcopy; URL assumed to
follow BSE's documented download-archive pattern:

    https://www.bseindia.com/download/BhavCopy/Derivative/BhavCopy_BSE_FO_0_0_0_{YYYYMMDD}_F_0000.CSV

This is a **lower-confidence** assumption than the NSE URL — BSE's archive
paths have changed more often historically. Verify against a real download
before relying on `fetch_bhavcopy_bse`.
"""

from __future__ import annotations

import datetime as dt

import httpx
import sqlalchemy as sa

from te.data._udiff_parser import parse_udiff_fo_csv
from te.data.bhav_ingest import HEADERS as _HEADERS
from te.data.bhav_ingest import ingest as _ingest
from te.data.bhav_types import OptionBhavRow

SOURCE = "bse_bhavcopy"
EXCHANGE = "BFO"

#: Index option underlyings traded on BSE.
BSE_INDEX_SYMBOLS = frozenset({"SENSEX", "BANKEX"})

_URL_TEMPLATE = "https://www.bseindia.com/download/BhavCopy/Derivative/BhavCopy_BSE_FO_0_0_0_{date:%Y%m%d}_F_0000.CSV"


def parse_bhavcopy_bse(csv_text: str) -> list[OptionBhavRow]:
    """Parses a BSE derivatives UDiFF bhavcopy CSV body into index-option
    rows (SENSEX/BANKEX only)."""
    return parse_udiff_fo_csv(
        csv_text,
        exchange=EXCHANGE,
        source=SOURCE,
        allowed_symbols=BSE_INDEX_SYMBOLS,
    )


def fetch_bhavcopy_bse(trade_date: dt.date, *, timeout: float = 30.0) -> str:
    """Downloads BSE's derivatives bhavcopy CSV for `trade_date`. NOT
    verified against a live BSE download in this sandbox — see module
    docstring."""
    url = _URL_TEMPLATE.format(date=trade_date)
    response = httpx.get(url, headers=_HEADERS, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.text


def ingest_bhavcopy_bse(engine: sa.Engine, trade_date: dt.date) -> list[OptionBhavRow]:
    """Fetches, parses, and records an ingest-log row for one trading day's
    BSE derivatives bhavcopy. Returns the parsed rows (a "failed" log row on
    any fetch/parse error, re-raised after logging)."""
    return _ingest(
        engine,
        trade_date,
        source=SOURCE,
        fetch=lambda d: fetch_bhavcopy_bse(d),
        parse=parse_bhavcopy_bse,
    )
