"""NSE F&O daily bhavcopy ingestion — the only source of expired-option
history (Angel One/OpenAlgo cannot serve it once a contract expires; see
the plan's "Broker/data path" note).

**Fetch URL is unverified against a live download in this sandbox** (no
network egress — see the accompanying report). Built from NSE's documented
post-2024 UDiFF archive pattern on `nsearchives.nseindia.com`:

    https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip

Verify this exact filename against a real download before relying on
`fetch_bhavcopy_nse` for anything beyond a manual smoke test — the parser
logic (`parse_bhavcopy_nse`) is what's covered by the golden-file test and
is correct independent of the URL.
"""

from __future__ import annotations

import datetime as dt
import io
import zipfile

import httpx
import sqlalchemy as sa

from te.data._udiff_parser import parse_udiff_fo_csv
from te.data.bhav_ingest import HEADERS as _HEADERS
from te.data.bhav_ingest import ingest as _ingest
from te.data.bhav_types import OptionBhavRow

SOURCE = "nse_bhavcopy"
EXCHANGE = "NFO"

#: Index option underlyings traded on NSE (SENSEX/BANKEX are BSE-only, see
#: bhavcopy_bse.py).
NSE_INDEX_SYMBOLS = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"})

_URL_TEMPLATE = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{date:%Y%m%d}_F_0000.csv.zip"


def parse_bhavcopy_nse(csv_text: str) -> list[OptionBhavRow]:
    """Parses an NSE F&O UDiFF bhavcopy CSV body into index-option rows
    (NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY only)."""
    return parse_udiff_fo_csv(
        csv_text,
        exchange=EXCHANGE,
        source=SOURCE,
        allowed_symbols=NSE_INDEX_SYMBOLS,
    )


def fetch_bhavcopy_nse(trade_date: dt.date, *, timeout: float = 30.0) -> str:
    """Downloads and unzips the NSE F&O bhavcopy for `trade_date`, returning
    the raw CSV text. NOT verified against a live NSE download in this
    sandbox — see module docstring."""
    url = _URL_TEMPLATE.format(date=trade_date)
    response = httpx.get(url, headers=_HEADERS, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        names = zf.namelist()
        if not names:
            raise ValueError(f"NSE bhavcopy zip for {trade_date} was empty: {url}")
        return zf.read(names[0]).decode("utf-8")


def ingest_bhavcopy_nse(engine: sa.Engine, trade_date: dt.date) -> list[OptionBhavRow]:
    """Fetches, parses, and records an ingest-log row for one trading day's
    NSE F&O bhavcopy. Returns the parsed rows (a "failed" log row on any
    fetch/parse error, re-raised after logging)."""
    return _ingest(
        engine,
        trade_date,
        source=SOURCE,
        fetch=lambda d: fetch_bhavcopy_nse(d),
        parse=parse_bhavcopy_nse,
    )
