"""Golden-file test for te.data.bhavcopy_bse.parse_bhavcopy_bse (SENSEX/
BANKEX). Constructed golden file, not a live BSE download — see
`bhavcopy_bse.py`'s module docstring."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.data.bhavcopy_bse import BSE_INDEX_SYMBOLS, parse_bhavcopy_bse

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "bhavcopy_bse_fo_golden.csv"


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
