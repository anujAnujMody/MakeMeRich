"""`BacktestFillEngine` — the backtest fill path. Imports and uses
`te.broker.simulated.SimulatedBroker` DIRECTLY, never a second cost/fill
calculation — the plan's explicit guarantee that backtest and paper trading
can never silently diverge on cost/fill assumptions
(`tests/backtest/test_fills.py::test_backtest_and_paper_produce_identical_fill_for_same_input`
proves this).
"""

from __future__ import annotations

from decimal import Decimal

from te.broker.protocol import FillReport
from te.broker.simulated import SimulatedBroker
from te.domain.costs import CostModel
from te.domain.orders import OrderIntent

#: Default slippage floor, in basis points, applied to every backtest fill.
#:
#: **Deliberately non-zero.** This defaulted to `Decimal(0)` and no caller
#: ever passed anything else, so a backtest modelled perfect fills and
#: overstated every result it produced.
#:
#: 2 bps is the figure the `vectorbt-expert` skill records for NIFTY/BANKNIFTY
#: index derivatives (`rules/pitfalls.md`: "For futures, `slippage=0.0002`
#: (0.02%) is reasonable for NIFTY/BANKNIFTY"). Treat it as a FLOOR, not an
#: estimate: it is sourced from index FUTURES, and option premiums are far
#: wider-spread instruments, so real option slippage is very likely higher.
#: Callers with a better-grounded figure should pass `slippage_bps=`
#: explicitly rather than rely on this.
DEFAULT_SLIPPAGE_BPS = Decimal(2)


class BacktestFillEngine:
    """Fills one `OrderIntent` at a time through a fresh `SimulatedBroker`
    (SimulatedBroker is constructed with a trade `date` since `CostModel`
    rates are versioned by `effective_from` — see `te.domain.costs`), so
    every fill in a backtest goes through the exact same
    `SimulatedBroker.place_order()` -> `CostModel.leg()` code path paper
    trading uses. Deliberately stateless across calls (a fresh broker per
    fill) — the backtest engine owns position/PnL bookkeeping itself
    (`te.backtest.engine`), this class's only job is "what does this one
    order intent fill at, net of cost, on this date"."""

    def __init__(self, *, cost_model: CostModel, slippage_bps: Decimal = DEFAULT_SLIPPAGE_BPS) -> None:
        self._cost_model = cost_model
        self._slippage_bps = slippage_bps

    def fill(self, intent: OrderIntent) -> FillReport:
        broker = SimulatedBroker(cost_model=self._cost_model, on=intent.ts.date(), slippage_bps=self._slippage_bps)
        broker.place_order(intent)
        reports = broker.fill_reports()
        assert len(reports) == 1  # one place_order() call always yields exactly one fill in this venue model
        return reports[0]
