"""Option symbol build/parse and expiry calendar — logic only, no network
calls. Symbol grammar matches OpenAlgo's standardised convention (see
`.agents/skills/openalgo/references/symbol-format.md`):

    Options:  <BASE><DD><MMM><YY><STRIKE><CE|PE>

e.g. `NIFTY30JUN2626500CE` (NIFTY 26500 CE, expiring 30-Jun-2026).

Live expiry confirmation from the broker (lot sizes, exact contract
availability) belongs to `te/broker` in a later phase — this module only
computes, given a reference date, what the next expiry *should* be under the
current NSE/BSE calendar rules.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

OptionType = Literal["CE", "PE"]

_MONTH_ABBR = {
    1: "JAN",
    2: "FEB",
    3: "MAR",
    4: "APR",
    5: "MAY",
    6: "JUN",
    7: "JUL",
    8: "AUG",
    9: "SEP",
    10: "OCT",
    11: "NOV",
    12: "DEC",
}
_ABBR_TO_MONTH = {v: k for k, v in _MONTH_ABBR.items()}

_OPTION_SYMBOL_RE = re.compile(
    r"^(?P<base>[A-Z]+)(?P<day>\d{2})(?P<mon>[A-Z]{3})(?P<yy>\d{2})(?P<strike>\d+(?:\.\d+)?)(?P<type>CE|PE)$"
)

# Weekly-expiry-eligible indices and their exchange's single weekly expiry
# weekday (post Sep-2025 regime: NIFTY -> Tuesday on NSE, SENSEX -> Thursday
# on BSE; `date.weekday()` convention, Monday=0).
_WEEKLY_EXPIRY_WEEKDAY = {"NIFTY": 1, "SENSEX": 3}

# Indices that trade monthly only, on their own exchange's single weekly
# expiry weekday (BANKNIFTY/FINNIFTY/MIDCPNIFTY on NSE -> Tuesday; BANKEX on
# BSE -> Thursday).
_MONTHLY_ONLY_EXPIRY_WEEKDAY = {"BANKNIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 1, "BANKEX": 3}


def fmt_expiry(expiry: dt.date) -> str:
    """`date(2026, 6, 30) -> "30JUN26"`."""
    return f"{expiry.day:02d}{_MONTH_ABBR[expiry.month]}{expiry.year % 100:02d}"


def _fmt_strike(strike: Decimal | int | float) -> str:
    value = strike if isinstance(strike, Decimal) else Decimal(str(strike))
    normalized = value.normalize()
    # Decimal.normalize() can produce exponent form (e.g. "2.65E+4") for
    # whole numbers; strings without a fractional part are the common case.
    if normalized == normalized.to_integral_value():
        return str(int(normalized))
    return format(normalized, "f")


def build_option_symbol(base: str, expiry: dt.date, strike: Decimal | int | float, option_type: OptionType) -> str:
    return f"{base}{fmt_expiry(expiry)}{_fmt_strike(strike)}{option_type}"


@dataclass(frozen=True)
class ParsedOptionSymbol:
    base: str
    expiry: dt.date
    strike: Decimal
    option_type: OptionType
    symbol: str


def parse_option_symbol(symbol: str) -> ParsedOptionSymbol:
    match = _OPTION_SYMBOL_RE.match(symbol)
    if match is None:
        raise ValueError(f"{symbol!r} is not a recognised option symbol (expected <BASE><DD><MMM><YY><STRIKE><CE|PE>)")
    month = _ABBR_TO_MONTH.get(match["mon"])
    if month is None:
        raise ValueError(f"{symbol!r} has an unrecognised month abbreviation: {match['mon']!r}")
    year = 2000 + int(match["yy"])
    expiry = dt.date(year, month, int(match["day"]))
    option_type: OptionType = "CE" if match["type"] == "CE" else "PE"
    return ParsedOptionSymbol(
        base=match["base"], expiry=expiry, strike=Decimal(match["strike"]), option_type=option_type, symbol=symbol
    )


def _next_weekday_on_or_after(reference: dt.date, weekday: int) -> dt.date:
    delta = (weekday - reference.weekday()) % 7
    return reference + dt.timedelta(days=delta)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> dt.date:
    if month == 12:
        next_month_first = dt.date(year + 1, 1, 1)
    else:
        next_month_first = dt.date(year, month + 1, 1)
    last_day = next_month_first - dt.timedelta(days=1)
    delta = (last_day.weekday() - weekday) % 7
    return last_day - dt.timedelta(days=delta)


def next_weekly_expiry(base: str, reference: dt.date) -> dt.date:
    """Next weekly expiry on or after `reference`. Only NIFTY (NSE, Tuesday)
    and SENSEX (BSE, Thursday) trade weekly under the post Nov-2024/Sep-2025
    regime — one weekly expiry per exchange."""
    weekday = _WEEKLY_EXPIRY_WEEKDAY.get(base)
    if weekday is None:
        raise ValueError(f"{base!r} has no weekly expiry; only NIFTY/SENSEX trade weekly")
    return _next_weekday_on_or_after(reference, weekday)


def next_monthly_expiry(base: str, reference: dt.date) -> dt.date:
    """Next monthly expiry on or after `reference` — the last occurrence of
    the index's exchange's weekly weekday in a calendar month. Applies to
    BANKNIFTY/FINNIFTY/MIDCPNIFTY (NSE, Tuesday) and BANKEX (BSE, Thursday),
    which trade monthly only."""
    weekday = _MONTHLY_ONLY_EXPIRY_WEEKDAY.get(base) or _WEEKLY_EXPIRY_WEEKDAY.get(base)
    if weekday is None:
        raise ValueError(f"{base!r} has no known expiry weekday")
    candidate = _last_weekday_of_month(reference.year, reference.month, weekday)
    if candidate >= reference:
        return candidate
    if reference.month == 12:
        return _last_weekday_of_month(reference.year + 1, 1, weekday)
    return _last_weekday_of_month(reference.year, reference.month + 1, weekday)
