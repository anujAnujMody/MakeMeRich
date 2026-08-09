import datetime as dt
from decimal import Decimal

import pytest

from te.domain.symbols import (
    build_option_symbol,
    fmt_expiry,
    next_monthly_expiry,
    next_weekly_expiry,
    parse_option_symbol,
)


def test_fmt_expiry() -> None:
    assert fmt_expiry(dt.date(2026, 6, 30)) == "30JUN26"


def test_build_option_symbol_integer_strike() -> None:
    assert build_option_symbol("NIFTY", dt.date(2026, 6, 30), 26500, "CE") == "NIFTY30JUN2626500CE"


def test_build_option_symbol_decimal_strike() -> None:
    assert build_option_symbol("VEDL", dt.date(2026, 6, 30), Decimal("292.5"), "PE") == "VEDL30JUN26292.5PE"


def test_parse_option_symbol_round_trips() -> None:
    parsed = parse_option_symbol("NIFTY30JUN2626500CE")
    assert parsed.base == "NIFTY"
    assert parsed.expiry == dt.date(2026, 6, 30)
    assert parsed.strike == Decimal("26500")
    assert parsed.option_type == "CE"
    assert parsed.symbol == "NIFTY30JUN2626500CE"


def test_parse_option_symbol_decimal_strike() -> None:
    parsed = parse_option_symbol("VEDL30JUN26292.5PE")
    assert parsed.base == "VEDL"
    assert parsed.strike == Decimal("292.5")
    assert parsed.option_type == "PE"


def test_parse_option_symbol_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="option symbol"):
        parse_option_symbol("NOT-AN-OPTION-SYMBOL")


# --- expiry calendar ---


def test_nifty_next_weekly_expiry_is_next_tuesday() -> None:
    # 2026-07-29 is a Wednesday
    reference = dt.date(2026, 7, 29)
    expiry = next_weekly_expiry("NIFTY", reference)
    assert expiry.weekday() == 1  # Tuesday
    assert expiry == dt.date(2026, 8, 4)


def test_nifty_next_weekly_expiry_on_expiry_day_itself() -> None:
    reference = dt.date(2026, 8, 4)  # a Tuesday
    assert next_weekly_expiry("NIFTY", reference) == dt.date(2026, 8, 4)


def test_sensex_next_weekly_expiry_is_next_thursday() -> None:
    # 2026-07-29 is a Wednesday
    reference = dt.date(2026, 7, 29)
    expiry = next_weekly_expiry("SENSEX", reference)
    assert expiry.weekday() == 3  # Thursday
    assert expiry == dt.date(2026, 7, 30)


def test_banknifty_has_no_weekly_expiry() -> None:
    with pytest.raises(ValueError, match="weekly"):
        next_weekly_expiry("BANKNIFTY", dt.date(2026, 7, 29))


def test_banknifty_next_monthly_expiry_rolls_forward_when_already_past() -> None:
    reference = dt.date(2026, 7, 29)  # after Jul's last Tuesday (Jul 28)
    expiry = next_monthly_expiry("BANKNIFTY", reference)
    assert expiry == dt.date(2026, 8, 25)  # last Tuesday of August 2026


def test_bankex_next_monthly_expiry_is_last_thursday_of_month() -> None:
    reference = dt.date(2026, 7, 1)
    expiry = next_monthly_expiry("BANKEX", reference)
    assert expiry.weekday() == 3  # Thursday
    assert expiry == dt.date(2026, 7, 30)  # last Thursday of July 2026


def test_banknifty_next_monthly_expiry_on_expiry_day_itself() -> None:
    """The monthly path's twin of `test_nifty_next_weekly_expiry_on_expiry_day_itself`
    — `reference == expiry` exactly, which neither existing monthly fixture
    covers (one is a day past expiry, the other is the 1st of the month).
    2026-07-28 is BANKNIFTY's last Tuesday of July."""
    reference = dt.date(2026, 7, 28)  # a Tuesday, and July's last one
    assert next_monthly_expiry("BANKNIFTY", reference) == reference
