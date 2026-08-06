"""India VIX level + near/next month term structure, via OpenAlgo's quote API.

Regime detection (`te/risk/regime.py`, a later phase) uses `vix_term_slope`
as a **size throttle only, never a signal generator** — see the plan's
"Meta-labeling pipeline" section. This module only fetches and computes the
raw numbers; it makes no trading decisions.

Symbol assumptions (NOT verified against a live OpenAlgo/NSE feed in this
sandbox — see the accompanying report):
- Spot India VIX: `symbol="INDIAVIX"`, `exchange="NSE_INDEX"`.
- India VIX futures near/next month contracts follow NSE's standard
  `<SYMBOL><DD><MON><YY>FUT` convention, e.g. `INDIAVIX28AUG25FUT`. This
  module accepts explicit near/next symbols rather than guessing the
  current month internally, so the caller (which knows "today") supplies
  them.
"""

from __future__ import annotations

from dataclasses import dataclass

from te.broker.openalgo_rest import OpenAlgoRestClient


@dataclass(frozen=True, slots=True)
class VixSnapshot:
    level: float
    near_month: float | None
    next_month: float | None
    term_slope: float | None  # (next - near) / near; None if either leg missing


def get_vix_snapshot(
    client: OpenAlgoRestClient,
    *,
    near_month_symbol: str | None = None,
    next_month_symbol: str | None = None,
    exchange: str = "NSE_INDEX",
) -> VixSnapshot:
    """Fetches India VIX spot level and, if near/next month futures symbols
    are supplied, their LTPs and the resulting term slope."""
    spot = client.quotes(symbol="INDIAVIX", exchange=exchange)

    near = client.quotes(symbol=near_month_symbol, exchange="NFO").ltp if near_month_symbol else None
    next_ = client.quotes(symbol=next_month_symbol, exchange="NFO").ltp if next_month_symbol else None

    slope = (next_ - near) / near if (near is not None and next_ is not None and near != 0) else None

    return VixSnapshot(level=spot.ltp, near_month=near, next_month=next_, term_slope=slope)
