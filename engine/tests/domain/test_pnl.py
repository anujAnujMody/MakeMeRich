import ast
import inspect

from te.domain import pnl as pnl_module
from te.domain.costs import CostBreakdown
from te.domain.money import Paise
from te.domain.pnl import net_pnl


def _cost_breakdown(total: int) -> CostBreakdown:
    return CostBreakdown(
        brokerage=Paise(total), stt=Paise(0), exchange_txn=Paise(0), sebi=Paise(0), gst=Paise(0), stamp=Paise(0)
    )


def test_net_pnl_is_entry_minus_exit_minus_costs() -> None:
    costs = _cost_breakdown(6511)
    result = net_pnl(entry_fill_paise=Paise(10_000), exit_fill_paise=Paise(12_000), qty=65, costs=costs)
    gross = 65 * (12_000 - 10_000)
    assert result == gross - 6511
    assert result == 123_489  # ₹1,234.89, matching the plan's worked example


def test_net_pnl_is_negative_when_costs_exceed_gross() -> None:
    costs = _cost_breakdown(700)
    result = net_pnl(entry_fill_paise=Paise(2_000), exit_fill_paise=Paise(2_010), qty=65, costs=costs)
    assert result < 0


def test_net_pnl_is_the_only_netpnl_constructor() -> None:
    """Structural check: `NetPnl(...)` may only be called inside
    `net_pnl()` itself — anywhere else in this module (or elsewhere) it
    would bypass the cost-deduction it exists to enforce."""
    source = inspect.getsource(pnl_module)
    tree = ast.parse(source)

    netpnl_calls: list[ast.Call] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 (ast API)
            if isinstance(node.func, ast.Name) and node.func.id == "NetPnl":
                netpnl_calls.append(node)
            self.generic_visit(node)

    _Visitor().visit(tree)
    assert len(netpnl_calls) == 1, "NetPnl(...) must be constructed exactly once, inside net_pnl()"

    net_pnl_def = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "net_pnl")
    assert net_pnl_def.lineno <= netpnl_calls[0].lineno <= net_pnl_def.end_lineno  # type: ignore[operator]


def test_net_pnl_result_is_a_netpnl_instance() -> None:
    costs = _cost_breakdown(100)
    result = net_pnl(entry_fill_paise=Paise(10_000), exit_fill_paise=Paise(10_500), qty=1, costs=costs)
    assert isinstance(result, int)  # NetPnl is a Paise/int NewType at runtime
