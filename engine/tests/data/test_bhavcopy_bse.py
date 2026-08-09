"""Golden-file test for te.data.bhavcopy_bse.parse_bhavcopy_bse (SENSEX/
BANKEX). Constructed golden file, not a live BSE download — see
`bhavcopy_bse.py`'s module docstring."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.data.bhavcopy_bse import BSE_INDEX_SYMBOLS, parse_bhavcopy_bse

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "bhavcopy_bse_fo_golden.csv"
_HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,"
    "StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,"
    "SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,"
    "Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4"
)


def test_bhavcopy_bse_parses_sensex_option_row() -> None:
    rows = parse_bhavcopy_bse(_FIXTURE.read_text())

    sensex_ce = next(r for r in rows if r.symbol == "SENSEX" and r.option_type == "CE")
    assert sensex_ce.strike == 77800.0
    assert sensex_ce.expiry == dt.date(2026, 8, 27)
    assert sensex_ce.close == 113.0
    assert sensex_ce.open_interest == 410_000
    assert sensex_ce.exchange == "BFO"
    assert sensex_ce.source == "bse_bhavcopy"


def test_bhavcopy_bse_excludes_futures_rows() -> None:
    rows = parse_bhavcopy_bse(_FIXTURE.read_text())

    assert all(r.symbol in BSE_INDEX_SYMBOLS for r in rows)
    # 3 index-option rows in the fixture: SENSEX CE, SENSEX PE, BANKEX CE.
    assert len(rows) == 3


def test_bhavcopy_bse_raises_on_html_response_instead_of_silently_returning_empty() -> None:
    """Regression, found live on 2026-07-30: BSE's archive returns HTTP 200
    with its own homepage (not a 404) for any date outside its UDiFF
    coverage (pre-~Jan 2024) — confirmed against the real archive. Without
    this check, `csv.DictReader` over the HTML body just silently returns
    `[]`, indistinguishable from a real zero-row trading day."""
    html_body = "<!DOCTYPE html><html><head><title>BSE India</title></head><body>...</body></html>"
    with pytest.raises(ValueError, match="looks like HTML"):
        parse_bhavcopy_bse(html_body)


def test_a_blank_close_price_is_refused_by_name() -> None:
    """A blank `ClsPric` must never become a fabricated `0.0` — mirrors the
    NSE regression test; both exchanges share `_udiff_parser.py`."""
    row = (
        "29-JUL-2026,29-JUL-2026,FO,BSE,IDO,90002,,SENSEX,,27-AUG-2026,27-AUG-2026,77800,CE,"
        "SENSEX26AUG77800CE,120,128,105,,113,118,77508,113,410000,6000,18000,2034000,3500,F1,20,,,,,"
    )
    csv_text = "\n".join([_HEADER, row])

    with pytest.raises(ValueError, match="blank ClsPric"):
        parse_bhavcopy_bse(csv_text)


def test_a_blank_volume_is_refused_by_name() -> None:
    """A blank `TtlTradgVol` is unknown data, not a real no-trade-day zero."""
    row = (
        "29-JUL-2026,29-JUL-2026,FO,BSE,IDO,90002,,SENSEX,,27-AUG-2026,27-AUG-2026,77800,CE,"
        "SENSEX26AUG77800CE,120,128,105,113,113,118,77508,113,410000,6000,,2034000,3500,F1,20,,,,,"
    )
    csv_text = "\n".join([_HEADER, row])

    with pytest.raises(ValueError, match="blank TtlTradgVol"):
        parse_bhavcopy_bse(csv_text)
