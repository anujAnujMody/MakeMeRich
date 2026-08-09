"""Golden-file test for te.data.bhavcopy_nse.parse_bhavcopy_nse.

Verifies strike/expiry/option_type/OHLC/OI parse correctly from a
realistic sample row, and that non-index-option rows (futures, stock
options) are filtered out. The fixture CSV is a **constructed golden
file**, not a live NSE download — see `bhavcopy_nse.py`'s module docstring
for exactly what's unverified.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.data.bhavcopy_nse import NSE_INDEX_SYMBOLS, parse_bhavcopy_nse

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "bhavcopy_nse_fo_golden.csv"
_HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,"
    "StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,"
    "SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,"
    "Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4"
)


def test_bhavcopy_parses_option_row() -> None:
    csv_text = _FIXTURE.read_text()
    rows = parse_bhavcopy_nse(csv_text)

    nifty_ce = next(r for r in rows if r.symbol == "NIFTY" and r.option_type == "CE")
    assert nifty_ce.strike == 24500.0
    assert nifty_ce.expiry == dt.date(2026, 8, 5)
    assert nifty_ce.trade_date == dt.date(2026, 7, 29)
    assert nifty_ce.open == 38.0
    assert nifty_ce.high == 45.0
    assert nifty_ce.low == 32.0
    assert nifty_ce.close == 35.0
    assert nifty_ce.settle_price == 34.75
    assert nifty_ce.open_interest == 850_000
    assert nifty_ce.change_in_oi == 12_000
    assert nifty_ce.volume == 45_000
    assert nifty_ce.exchange == "NFO"
    assert nifty_ce.source == "nse_bhavcopy"


def test_bhavcopy_excludes_futures_and_stock_option_rows() -> None:
    rows = parse_bhavcopy_nse(_FIXTURE.read_text())

    assert all(r.symbol in NSE_INDEX_SYMBOLS for r in rows)
    assert all(r.option_type in ("CE", "PE") for r in rows)
    # 3 index-option rows in the fixture: NIFTY CE, NIFTY PE, BANKNIFTY CE.
    assert len(rows) == 3


def test_bhavcopy_includes_both_option_types() -> None:
    rows = parse_bhavcopy_nse(_FIXTURE.read_text())
    option_types = {r.option_type for r in rows}
    assert option_types == {"CE", "PE"}


def test_a_blank_close_price_is_refused_by_name() -> None:
    """A blank `ClsPric` on an otherwise-priced index-option row must never
    become a fabricated `0.0` — `option_bhav.close` is `nullable=False`, so
    nothing downstream could ever tell a real zero from a missing value."""
    row = (
        "29-JUL-2026,29-JUL-2026,FO,NSE,IDO,45002,,NIFTY,,05-AUG-2026,05-AUG-2026,24500,CE,"
        "NIFTY26AUG24500CE,38,45,32,,35,42,24211,35,850000,12000,45000,15750000,8000,F1,65,,,,,"
    )
    csv_text = "\n".join([_HEADER, row])

    with pytest.raises(ValueError, match="blank ClsPric"):
        parse_bhavcopy_nse(csv_text)


def test_a_blank_volume_is_refused_by_name() -> None:
    """Same for `TtlTradgVol` — a blank volume is unknown data, not a
    no-trade day (that is a real `0`, handled elsewhere)."""
    row = (
        "29-JUL-2026,29-JUL-2026,FO,NSE,IDO,45002,,NIFTY,,05-AUG-2026,05-AUG-2026,24500,CE,"
        "NIFTY26AUG24500CE,38,45,32,35,35,42,24211,35,850000,12000,,15750000,8000,F1,65,,,,,"
    )
    csv_text = "\n".join([_HEADER, row])

    with pytest.raises(ValueError, match="blank TtlTradgVol"):
        parse_bhavcopy_nse(csv_text)
