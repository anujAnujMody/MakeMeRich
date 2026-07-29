"""Money as integer paise — the only unit any domain function may accept or
return. A bare `float` for money anywhere in `te.domain` is a bug: mypy
--strict on `Paise = NewType("Paise", int)` catches passing a `float` where a
`Paise` is expected, and `rupees()`/`paise()` are the only conversion points,
kept at the I/O/display boundary.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import NewType

Paise = NewType("Paise", int)

_HUNDRED = Decimal(100)
_WHOLE_PAISE = Decimal(1)


def rupees(amount: Paise) -> Decimal:
    """Converts integer paise to a `Decimal` rupee amount, for display only."""
    return (Decimal(amount) / _HUNDRED).quantize(Decimal("0.01"))


def paise(amount: Decimal | int) -> Paise:
    """Converts a rupee amount (input boundary only) to integer paise,
    rounding half up to the nearest paise."""
    if isinstance(amount, int):
        return Paise(amount * 100)
    return Paise(int((amount * _HUNDRED).quantize(_WHOLE_PAISE, rounding=ROUND_HALF_UP)))
