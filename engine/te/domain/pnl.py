"""The net-ness type wall. `GrossPnl` and `NetPnl` are distinct `NewType`s
over `Paise` so `mypy --strict` makes passing a gross value where a net value
is expected a type error. `net_pnl()` is the ONLY constructor of `NetPnl` in
the codebase — see `tests/domain/test_pnl.py::test_net_pnl_is_the_only_netpnl_constructor`.
"""

from __future__ import annotations

import datetime as dt
from typing import NewType

from te.domain.costs import CostBreakdown, CostModel
from te.domain.money import Paise

GrossPnl = NewType("GrossPnl", Paise)
NetPnl = NewType("NetPnl", Paise)


def net_pnl(entry_fill_paise: Paise, exit_fill_paise: Paise, qty: int, costs: CostBreakdown) -> NetPnl:
    """The ONLY constructor of `NetPnl` in the codebase. Gross P&L minus the
    full itemised cost breakdown's total — never gross, never an estimate."""
    gross = Paise((exit_fill_paise - entry_fill_paise) * qty)
    return NetPnl(Paise(gross - costs.total))


def mark_to_market_pnl(
    *,
    entry_premium: Paise,
    current_premium: Paise,
    qty: int,
    exchange: str,
    cost_model: CostModel,
    on: dt.date,
) -> NetPnl:
    """What an open position's net P&L would be if closed right now at
    `current_premium` — the exact same round-trip-cost formula a real close
    uses (`te.engine.cycle._close_position`), so a displayed "unrealized
    P&L" or an unrealized-loss risk check can never quietly diverge from
    what actually happens on exit. Takes plain scalars rather than an
    `OpenPositionRow` so both `te.risk.limits` (below `te.engine` in the
    layer rule) and `te.engine.cycle`/the API layer (above it) can call the
    same formula without a layer violation either way."""
    costs = cost_model.round_trip(
        entry_premium=entry_premium, exit_premium=current_premium, qty=qty, exchange=exchange, on=on
    )
    return net_pnl(entry_premium, current_premium, qty, costs)
