"""Shared parser for NSE/BSE's UDiFF-format daily F&O bhavcopy CSV.

Both exchanges publish a "Unified Daily bhavcopy Interface File" (UDiFF)
per SEBI's unified-format mandate — same column layout, `FinInstrmTp` codes,
and date format on both NSE and BSE derivatives bhavcopy files. This module
is the one parser both `bhavcopy_nse.py` and `bhavcopy_bse.py` call, with
the exchange code / source label / instrument-type filter as parameters.

**Column layout is NOT verified against a live download in this sandbox**
(no network egress available — see the accompanying report). It is built
from the documented UDiFF column set (`TradDt`, `BizDt`, `Sgmt`, `Src`,
`FinInstrmTp`, `FinInstrmId`, `ISIN`, `TckrSymb`, `SctySrs`, `XpryDt`,
`FininstrmActlXpryDt`, `StrkPric`, `OptnTp`, `FinInstrmNm`, `OpnPric`,
`HghPric`, `LwPric`, `ClsPric`, `LastPric`, `PrvsClsgPric`, `UndrlygPric`,
`SttlmPric`, `OpnIntrst`, `ChngInOpnIntrst`, `TtlTradgVol`, `TtlTrfVal`,
`TtlNbOfTxsExctd`, `SsnId`, `NewBrdLotQty`, `Rmks`, `Rsvd1..4`) that NSE
adopted for its post-2024 archive format on `nsearchives.nseindia.com`.
Verify against a real downloaded file before relying on this for anything
beyond parser-logic tests.

`FinInstrmTp` codes used to filter to index options only:
`"IDO"` = Index Options (what this project needs — NIFTY/BANKNIFTY/SENSEX/
BANKEX index option buying only, per the plan). `"IDF"` (index futures) and
`"STO"`/`"STF"` (stock options/futures) rows are skipped.
"""

from __future__ import annotations

import csv
import datetime as dt
import io

from te.data.bhav_types import OptionBhavRow

INDEX_OPTION_INSTRUMENT_TYPE = "IDO"

_DATE_FORMATS = ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y")


def _parse_date(raw: str) -> dt.date:
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised bhavcopy date format: {raw!r}")


def _to_int(raw: str, *, column: str, context: str) -> int:
    if not raw.strip():
        raise ValueError(
            f"{context}: blank {column}. A priced index-option row with no {column} is "
            "unknown data, not a real zero — refusing rather than substituting a fabricated "
            "value that would be indistinguishable from a genuine one downstream."
        )
    return int(float(raw))


def _to_float(raw: str, *, column: str, context: str) -> float:
    if not raw.strip():
        raise ValueError(
            f"{context}: blank {column}. A priced index-option row with no {column} is "
            "unknown data, not a real zero — refusing rather than substituting a fabricated "
            "value that would be indistinguishable from a genuine one downstream."
        )
    return float(raw)


def parse_udiff_fo_csv(
    csv_text: str,
    *,
    exchange: str,
    source: str,
    allowed_symbols: frozenset[str] | None = None,
) -> list[OptionBhavRow]:
    """Parses a UDiFF F&O bhavcopy CSV body into `OptionBhavRow`s, keeping
    only index option (`FinInstrmTp == "IDO"`) rows whose `TckrSymb` is in
    `allowed_symbols` (all index-option rows if `None`).

    Raises on HTML input rather than silently returning an empty list.
    Found live on 2026-07-30: BSE's archive returns HTTP 200 with its own
    homepage (`Content-Type: text/html`) for any date its UDiFF bhavcopy
    doesn't cover, rather than a 404 — `response.raise_for_status()` never
    catches that, and `csv.DictReader` over an HTML body just finds no
    `FinInstrmTp`/`TckrSymb` columns on any "row", silently producing `[]`.
    That's indistinguishable from a real (rare) zero-index-option trading
    day without this check — exactly the silent-data-loss shape as the
    recorder's epoch-ms/ns timestamp bug found the same day."""
    stripped = csv_text.lstrip()
    if stripped.startswith("<"):
        raise ValueError(
            "bhavcopy response looks like HTML, not CSV — the exchange likely has no "
            "UDiFF bhavcopy for this date (out of archive range, or a market holiday) "
            "and returned its website's homepage with a 200 instead of a 404"
        )

    reader = csv.DictReader(io.StringIO(csv_text))
    rows: list[OptionBhavRow] = []

    for line_no, raw in enumerate(reader, start=2):  # line 1 is the header
        instrument_type = (raw.get("FinInstrmTp") or "").strip()
        if instrument_type != INDEX_OPTION_INSTRUMENT_TYPE:
            continue

        symbol = (raw.get("TckrSymb") or "").strip()
        if allowed_symbols is not None and symbol not in allowed_symbols:
            continue

        option_type = (raw.get("OptnTp") or "").strip()
        if option_type not in ("CE", "PE"):
            continue

        context = f"row {line_no} ({symbol} {option_type} strike={(raw.get('StrkPric') or '').strip()!r})"

        rows.append(
            OptionBhavRow(
                trade_date=_parse_date(raw["TradDt"]),
                symbol=symbol,
                expiry=_parse_date(raw["XpryDt"]),
                strike=_to_float(raw["StrkPric"], column="StrkPric", context=context),
                option_type=option_type,  # type: ignore[arg-type]
                exchange=exchange,
                open=_to_float(raw["OpnPric"], column="OpnPric", context=context),
                high=_to_float(raw["HghPric"], column="HghPric", context=context),
                low=_to_float(raw["LwPric"], column="LwPric", context=context),
                close=_to_float(raw["ClsPric"], column="ClsPric", context=context),
                settle_price=_to_float(raw["SttlmPric"], column="SttlmPric", context=context),
                open_interest=_to_int(raw["OpnIntrst"], column="OpnIntrst", context=context),
                change_in_oi=_to_int(raw["ChngInOpnIntrst"], column="ChngInOpnIntrst", context=context),
                volume=_to_int(raw["TtlTradgVol"], column="TtlTradgVol", context=context),
                source=source,
            )
        )

    return rows
