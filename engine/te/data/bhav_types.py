"""Shared parsed-row shape for NSE/BSE F&O bhavcopy ingestion.

Kept separate from `bhavcopy_nse.py`/`bhavcopy_bse.py` so both parsers (and
any future exchange) emit the exact same structure.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

OptionType = Literal["CE", "PE"]


@dataclass(frozen=True, slots=True)
class OptionBhavRow:
    """One parsed F&O bhavcopy row for an index option contract.

    `settle_price` is the exchange's daily settlement price (used for MTM,
    distinct from `close`, which can be stale/zero on a no-trade day) —
    this is what makes bhavcopy the only usable source of expired-option
    history at all: `close` alone is frequently 0 on illiquid far strikes.
    """

    trade_date: dt.date
    symbol: str  # underlying, e.g. "NIFTY", "BANKNIFTY", "SENSEX", "BANKEX"
    expiry: dt.date
    strike: float
    option_type: OptionType
    exchange: str  # "NFO" or "BFO"
    open: float
    high: float
    low: float
    close: float
    settle_price: float
    open_interest: int
    change_in_oi: int
    volume: int
    source: str  # "nse_bhavcopy" or "bse_bhavcopy"
