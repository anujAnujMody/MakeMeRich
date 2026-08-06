"""Shoonya <-> OpenAlgo option symbol conversion.

The historical option archive (`data/raw/shoonya/*.zip`) writes the option
TYPE before the strike; this repo's canonical grammar writes it after. Every
bar loaded from that archive is keyed by a symbol produced by this
conversion, so a silent mismatch here would file real premiums under a
symbol nothing ever reads — the labeller would fall back to the index
approximation and report it as a data gap rather than a bug.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from te.domain.symbols import (
    build_option_symbol,
    build_shoonya_option_symbol,
    parse_option_symbol,
    parse_shoonya_option_symbol,
)

# Both forms are REAL, taken from the archive: `CE`/`PE` up to expiry
# 2026-03-02, single-letter `C`/`P` from 2026-03-10 onward.
LONG_FORM = "NIFTY04JAN24CE18300"
SHORT_FORM = "NIFTY05MAY26P24500"


def test_parses_long_form_shoonya_symbol() -> None:
    parsed = parse_shoonya_option_symbol(LONG_FORM)
    assert parsed.base == "NIFTY"
    assert parsed.expiry == dt.date(2024, 1, 4)
    assert parsed.strike == Decimal(18300)
    assert parsed.option_type == "CE"


def test_parses_short_form_shoonya_symbol() -> None:
    parsed = parse_shoonya_option_symbol(SHORT_FORM)
    assert parsed.base == "NIFTY"
    assert parsed.expiry == dt.date(2026, 5, 5)
    assert parsed.strike == Decimal(24500)
    assert parsed.option_type == "PE"


@pytest.mark.parametrize(
    ("symbol", "short_type"),
    [
        (LONG_FORM, False),
        (SHORT_FORM, True),
        ("BANKNIFTY27JAN26PE55000", False),
        ("BANKNIFTY28APR26C60000", True),
    ],
)
def test_shoonya_symbol_round_trips(symbol: str, short_type: bool) -> None:
    parsed = parse_shoonya_option_symbol(symbol)
    rebuilt = build_shoonya_option_symbol(
        parsed.base, parsed.expiry, parsed.strike, parsed.option_type, short_type=short_type
    )
    assert rebuilt == symbol


@pytest.mark.parametrize("symbol", [LONG_FORM, SHORT_FORM])
def test_converts_to_canonical_and_back(symbol: str) -> None:
    """The load-bearing property: a Shoonya symbol becomes a canonical one
    that `parse_option_symbol` reads back with identical fields."""
    parsed = parse_shoonya_option_symbol(symbol)
    canonical = build_option_symbol(parsed.base, parsed.expiry, parsed.strike, parsed.option_type)
    back = parse_option_symbol(canonical)
    assert (back.base, back.expiry, back.strike, back.option_type) == (
        parsed.base,
        parsed.expiry,
        parsed.strike,
        parsed.option_type,
    )


def test_canonical_form_is_not_accepted_as_shoonya() -> None:
    """`NIFTY04JAN2418300CE` must NOT parse as Shoonya — accepting it would
    read `18300CE`'s leading digits as a type-less strike and silently
    produce a contract that never traded."""
    with pytest.raises(ValueError, match="not a recognised Shoonya"):
        parse_shoonya_option_symbol("NIFTY04JAN2418300CE")


def test_rejects_unknown_month() -> None:
    with pytest.raises(ValueError, match="month"):
        parse_shoonya_option_symbol("NIFTY04XXX24CE18300")
