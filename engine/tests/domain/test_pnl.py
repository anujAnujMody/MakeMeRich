import ast
import datetime as dt
import inspect
from decimal import Decimal
from pathlib import Path

from te.domain import pnl as pnl_module
from te.domain.costs import ChargeRates, CostBreakdown, CostModel
from te.domain.money import Paise
from te.domain.pnl import mark_to_market_pnl, net_pnl


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


def test_mark_to_market_pnl_uses_the_current_premium_not_the_entry_one() -> None:
    """No test anywhere exercised `mark_to_market_pnl` before this, though
    `te/risk/limits.py`'s unrealized-loss check and the positions API both
    call it. The confirmed historical bug shape (`CLAUDE.md`): an open
    position's ENTRY price returned as its "current" price, which would
    silently disable every price-based exit/limit — here that would show up
    as a flat `-costs` result no matter how far the premium actually moved.
    Pinned against a premium that fell sharply from entry, and cross-checked
    against `net_pnl` computed by hand from the same two prices."""
    rates = ChargeRates(
        effective_from=dt.date(2026, 4, 1),
        verified_at=dt.date(2026, 7, 29),
        brokerage_per_executed_order_paise=Paise(2000),
        stt_sell_bps=Decimal("15.0"),
        stt_exercise_intrinsic_bps=Decimal("15.0"),
        exchange_txn_bps={"NFO": Decimal("3.553"), "BFO": Decimal("3.25")},
        sebi_bps=Decimal("0.01"),
        gst_pct=Decimal("18.0"),
        stamp_buy_bps=Decimal("0.3"),
    )
    cost_model = CostModel(rates)
    on = dt.date(2026, 7, 29)
    entry_premium = Paise(16_680)
    current_premium = Paise(10_000)  # the premium has fallen sharply

    result = mark_to_market_pnl(
        entry_premium=entry_premium,
        current_premium=current_premium,
        qty=65,
        exchange="NFO",
        cost_model=cost_model,
        on=on,
    )

    expected_costs = cost_model.round_trip(
        entry_premium=entry_premium, exit_premium=current_premium, qty=65, exchange="NFO", on=on
    )
    expected = net_pnl(entry_fill_paise=entry_premium, exit_fill_paise=current_premium, qty=65, costs=expected_costs)
    assert result == expected
    assert result < 0, "a premium that fell from 16,680p to 10,000p must read as a real loss"

    # The bug this guards against — `exit_premium=entry_premium` swapped in
    # for `current_premium` — would drive the result to a flat -costs
    # regardless of how far the premium actually moved.
    flat_costs_if_entry_leaked_as_current = -cost_model.round_trip(
        entry_premium=entry_premium, exit_premium=entry_premium, qty=65, exchange="NFO", on=on
    ).total
    assert result < flat_costs_if_entry_leaked_as_current


def test_net_pnl_is_the_only_netpnl_constructor_in_this_module() -> None:
    """Structural check, scoped to this one module: `NetPnl(...)` may only
    be called inside `net_pnl()` itself — anywhere else in this module it
    would bypass the cost-deduction it exists to enforce.

    This does NOT prove the codebase-wide claim in the module docstring
    ("the ONLY constructor of `NetPnl` in the codebase") — a call in any
    other file is invisible to `inspect.getsource(pnl_module)`. See
    `test_net_pnl_is_the_only_netpnl_constructor_in_the_codebase` below for
    that check."""
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


def test_net_pnl_is_the_only_netpnl_constructor_in_the_codebase() -> None:
    """Codebase-wide version of the check above: walk EVERY `.py` file under
    `te/` and assert the only `NetPnl(...)` call anywhere lives inside
    `net_pnl()` in `te/domain/pnl.py`. A module-scoped AST check (like the
    test above, or the original version of this test) cannot see a bypass
    call in any other file — this is the check that actually backs the
    module docstring's "ONLY constructor ... in the codebase" claim."""
    te_root = Path(pnl_module.__file__).resolve().parents[1]
    assert te_root.name == "te"

    pnl_path = Path(pnl_module.__file__).resolve()

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.calls: list[ast.Call] = []

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 (ast API)
            if isinstance(node.func, ast.Name) and node.func.id == "NetPnl":
                self.calls.append(node)
            self.generic_visit(node)

    offenders: list[str] = []
    net_pnl_calls_in_pnl_py: list[ast.Call] = []
    net_pnl_def_bounds: tuple[int, int] | None = None

    for path in sorted(te_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _Visitor()
        visitor.visit(tree)
        if not visitor.calls:
            continue
        if path == pnl_path:
            net_pnl_calls_in_pnl_py = visitor.calls
            net_pnl_def = next(
                node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "net_pnl"
            )
            net_pnl_def_bounds = (net_pnl_def.lineno, net_pnl_def.end_lineno)  # type: ignore[assignment]
        else:
            offenders.append(f"{path}:{visitor.calls[0].lineno}")

    assert not offenders, f"NetPnl(...) constructed outside net_pnl() at: {offenders}"
    assert len(net_pnl_calls_in_pnl_py) == 1
    assert net_pnl_def_bounds is not None
    start, end = net_pnl_def_bounds
    assert start <= net_pnl_calls_in_pnl_py[0].lineno <= end
