"""The audit must not call the code it audits.

This is the single structural property that makes `te.audit.session_audit`
worth having. On 2026-08-07 the engine broke four of its own limits in one
session while 1,535 tests passed, because `check_max_trades_per_day` counted
closed trades and every test for it also counted closed trades. Code and test
shared one wrong idea, so they agreed.

An audit built on the same helpers would inherit the same wrong idea and
report "all clear" just as confidently. So this test reads the audit module's
own import statements and fails if it reaches for the engine's counters.

If this test ever becomes inconvenient, the answer is to stop and think, not
to relax it — the inconvenience IS the guarantee.
"""

from __future__ import annotations

import ast
from pathlib import Path

import te.audit.session_audit as audit_module

#: Modules whose whole job is the counting the audit exists to second-guess.
BANNED_MODULES = {
    "te.risk.limits",
    "te.risk.killswitch",
}

#: Names that must not be imported from anywhere, even a module not on the
#: list above. These are the specific aggregate helpers that carried the bug:
#: every one of them answers a question the audit must answer for itself.
BANNED_NAMES = {
    "trades_count_today",
    "trades_today",
    "underlying_entries_today",
    "open_positions_count",
    "open_positions",
    "evaluations_today",
    "order_ids_today",
    "daily_net_pnl_paise",
    "total_net_pnl_paise",
    "daily_pnl",
    "unrealized_pnl_paise",
    "check_max_trades_per_day",
    "check_consecutive_losses",
    "check_daily_loss_limit",
    "check_max_concurrent_positions",
    "consecutive_losses_breached",
}


def _imports(path: Path) -> list[tuple[str, str]]:
    """Every `(module, name)` the file imports. `name` is `"*"` for a bare
    `import x`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, "*") for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.extend((node.module, alias.name) for alias in node.names)
    return found


def test_audit_does_not_import_the_engines_counters() -> None:
    path = Path(audit_module.__file__)
    offenders = [
        f"{module}.{name}"
        for module, name in _imports(path)
        if module in BANNED_MODULES or name in BANNED_NAMES
    ]
    assert not offenders, (
        "te.audit.session_audit imported the very code it audits: "
        f"{offenders}. An audit that reuses a buggy counter reproduces the bug "
        "and reports all-clear — see this file's docstring."
    )


def test_the_ban_list_names_things_that_actually_exist() -> None:
    """Guards the guard. A typo in `BANNED_NAMES` (`trades_count_todya`) would
    silently make this whole file pass forever, which is exactly the class of
    dead test this project keeps finding. So confirm the real modules really do
    export the names being banned."""
    import te.engine.cycle as cycle
    import te.persistence.repos.paper_trading as repo
    import te.risk.limits as limits

    exported = set(dir(repo)) | set(dir(limits)) | set(dir(cycle))
    missing = BANNED_NAMES - exported
    assert not missing, (
        f"these banned names do not exist in the modules they were meant to guard: {missing}. "
        "Either they were renamed (update this list) or misspelled (fix the typo) — "
        "either way the ban is currently doing nothing."
    )
