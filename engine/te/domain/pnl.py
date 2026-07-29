"""The net-ness type wall. `GrossPnl` and `NetPnl` are distinct `NewType`s
over `Paise` so `mypy --strict` makes passing a gross value where a net value
is expected a type error. `net_pnl()` is the ONLY constructor of `NetPnl` in
the codebase — see `tests/domain/test_pnl.py::test_net_pnl_is_the_only_netpnl_constructor`.
"""

from __future__ import annotations

from typing import NewType

from te.domain.costs import CostBreakdown
from te.domain.money import Paise

GrossPnl = NewType("GrossPnl", Paise)
NetPnl = NewType("NetPnl", Paise)


def net_pnl(entry_fill_paise: Paise, exit_fill_paise: Paise, qty: int, costs: CostBreakdown) -> NetPnl:
    """The ONLY constructor of `NetPnl` in the codebase. Gross P&L minus the
    full itemised cost breakdown's total — never gross, never an estimate."""
    gross = Paise((exit_fill_paise - entry_fill_paise) * qty)
    return NetPnl(Paise(gross - costs.total))
