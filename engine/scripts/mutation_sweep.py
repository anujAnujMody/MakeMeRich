"""Mutation sweep — does the suite actually CATCH a wrong number?

This project's dominant failure is "the value is present, the type is correct,
and the MEANING is wrong", and its tests have repeatedly agreed with the bug
rather than caught it. A passing suite is therefore weak evidence. This asks the
only question that is strong evidence: **break the code on purpose, one operator
at a time, and see whether anything turns red.**

A mutant that survives is a hole. Every confirmed live bug in this codebase
would have shown up here first:

* `>=` -> `>` on a cap decides whether a compliant day reports a false violation
  or a real breach passes silently.
* dropping a `- costs.total` turns a net figure into a gross one, with the type
  unchanged and the number still plausible.
* `and` -> `or` in `bars_asof` disables half the point-in-time gate.

### Why not mutmut / cosmic-ray

`mutmut` refuses to run natively on Windows (WSL only). `cosmic-ray` runs, but
executes the WHOLE suite per mutant — at ~3 minutes a run and thousands of
mutants that is days of wall clock. The saving here is the `tests` mapping
below: a mutant in `te/risk/limits.py` only ever needs the risk tests, so a
mutant costs seconds. That is what makes the sweep repeatable rather than a
one-off.

### Safety

The mutated source is written to the REAL file, because that is the only way the
import graph sees it. Every write is bracketed by a `try/finally` restore from an
in-memory copy of the original bytes, plus an `atexit` hook, so a crash or a
Ctrl-C still puts the tree back. Run it on a clean tree and check `git status`
afterwards regardless — this rewrites your working files.

    python scripts/mutation_sweep.py                # every target
    python scripts/mutation_sweep.py te/risk        # one subtree
    python scripts/mutation_sweep.py --list         # count mutants, run nothing
"""

from __future__ import annotations

import argparse
import ast
import atexit
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: source glob -> the tests that must catch a mutant in it.
#:
#: Deliberately NOT "run everything": the whole point is that a mutant costs
#: seconds. But each entry must be WIDE enough to contain every test that
#: genuinely covers the module, or a mutant will be reported as surviving when
#: some untargeted test would have caught it. When in doubt, widen — a false
#: "survived" wastes an investigation, a false "killed" hides a hole.
TARGETS: dict[str, tuple[str, ...]] = {
    "te/domain/geometry.py": ("tests/domain", "tests/engine/test_exits.py"),
    "te/domain/costs.py": ("tests/domain", "tests/risk/test_sizing.py"),
    "te/domain/pnl.py": ("tests/domain", "tests/risk"),
    "te/domain/calendar.py": ("tests/domain/test_calendar.py", "tests/engine/test_trading_calendar.py"),
    "te/domain/clock.py": ("tests/domain", "tests/engine"),
    "te/domain/symbols.py": ("tests/domain/test_symbols.py", "tests/data"),
    "te/domain/orders.py": ("tests/domain/test_orders.py", "tests/execution"),
    "te/domain/signal.py": ("tests/domain", "tests/engine"),
    "te/risk/limits.py": ("tests/risk", "tests/engine/test_cycle.py"),
    "te/risk/sizing.py": ("tests/risk",),
    "te/risk/monitors.py": ("tests/risk",),
    "te/risk/live_gate.py": ("tests/risk/test_live_gate.py", "tests/api/test_mode_gate.py"),
    "te/engine/exits.py": ("tests/engine", "tests/backtest/test_engine.py"),
    "te/execution/manager.py": ("tests/execution",),
    "te/execution/inflight.py": ("tests/execution",),
    "te/execution/reconcile.py": ("tests/execution",),
    "te/persistence/repos/paper_trading.py": ("tests/risk", "tests/api", "tests/engine"),
    "te/data/asof.py": ("tests/data", "tests/leakage", "tests/strategy/test_context.py"),
    "te/data/_udiff_parser.py": ("tests/data",),
    "te/ml/gates.py": ("tests/ml",),
    "te/ml/metrics.py": ("tests/ml",),
}

#: Comparison and boolean operators. These carry the boundary decisions — every
#: `>` vs `>=` on a cap, every `and` holding two halves of a gate together.
_CMP = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt,
    ast.Gt: ast.GtE, ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
}
#: Arithmetic. `-` <-> `+` is how a net becomes a gross; `//` <-> `/` is how an
#: integer-paise truncation becomes a float.
_BIN = {
    ast.Add: ast.Sub, ast.Sub: ast.Add,
    ast.Mult: ast.FloorDiv, ast.FloorDiv: ast.Mult,
    ast.Div: ast.Mult,
}
_BOOL = {ast.And: ast.Or, ast.Or: ast.And}


@dataclass(frozen=True)
class Mutant:
    path: Path
    lineno: int
    col: int
    kind: str
    before: str
    after: str

    def label(self) -> str:
        rel = self.path.relative_to(ROOT).as_posix()
        return f"{rel}:{self.lineno} {self.kind} {self.before} -> {self.after}"


def _name(node: ast.AST) -> str:
    return type(node).__name__


class _Collector(ast.NodeVisitor):
    """Finds every mutable operator. Records the node's identity so the applier
    can mutate exactly one occurrence, even when a line holds several."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.found: list[tuple[ast.AST, int, str, str, str]] = []
        self._counter = 0

    def _record(self, node: ast.AST, op: ast.AST, table: dict, kind: str) -> None:
        """Counts ONLY operators that can actually be replaced.

        This used to increment on every operator it saw, while the applier's
        `_swap` returned early without incrementing for ones it could not
        replace. The two indexes drifted, so mutant N as labelled here and
        mutant N as applied there were different edits — the sweep reported
        line numbers that did not match what it had actually changed. Both
        sides now count the same things in the same order."""
        repl = table.get(type(op))
        if repl is None:
            return
        self.found.append((node, self._counter, kind, _name(op), repl.__name__))
        self._counter += 1

    def visit_Compare(self, node: ast.Compare) -> None:  # noqa: N802
        self.generic_visit(node)
        for op in node.ops:
            self._record(node, op, _CMP, "cmp")

    def visit_BinOp(self, node: ast.BinOp) -> None:  # noqa: N802
        self.generic_visit(node)
        self._record(node, node.op, _BIN, "bin")

    def visit_BoolOp(self, node: ast.BoolOp) -> None:  # noqa: N802
        self.generic_visit(node)
        self._record(node, node.op, _BOOL, "bool")


class _Applier(ast.NodeTransformer):
    """Applies the Nth mutation and no other."""

    def __init__(self, target_index: int) -> None:
        self.target = target_index
        self._counter = 0
        self.applied: tuple[str, str] | None = None

    def _swap(self, op: ast.AST, table: dict) -> ast.AST | None:
        repl = table.get(type(op))
        if repl is None:
            return None
        hit = self._counter == self.target
        self._counter += 1
        if not hit:
            return None
        self.applied = (_name(op), repl.__name__)
        return repl()

    def visit_Compare(self, node: ast.Compare) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        node.ops = [self._swap(op, _CMP) or op for op in node.ops]
        return node

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        node.op = self._swap(node.op, _BIN) or node.op
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        node.op = self._swap(node.op, _BOOL) or node.op
        return node


def mutants_for(path: Path) -> list[Mutant]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    collector = _Collector(path)
    collector.visit(tree)
    return [
        Mutant(path=path, lineno=node.lineno, col=node.col_offset, kind=kind, before=before, after=after)
        for node, _idx, kind, before, after in collector.found
    ]


def _mutated_source(original: str, index: int) -> str | None:
    tree = ast.parse(original)
    applier = _Applier(index)
    new_tree = applier.visit(tree)
    if applier.applied is None:
        return None
    ast.fix_missing_locations(new_tree)
    return ast.unparse(new_tree)


def _run_tests(tests: tuple[str, ...], basetemp: Path) -> bool:
    """True when the suite PASSES (mutant survived)."""
    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest", *tests,
            "-x", "-q", "--no-cov", "-p", "no:cacheprovider",
            "--basetemp", str(basetemp), "-p", "no:randomly",
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=1800,
    )
    return proc.returncode == 0


#: Guards against two sweeps running at once. Learned the hard way: a first run
#: was killed with a PID filter that missed one of its processes, and the orphan
#: kept mutating the same files while a second sweep ran. Both wrote the same
#: source, so the second run's results described a file neither of them
#: controlled — it reported a 98.1% score that meant nothing at all, and left
#: four files in `ast.unparse` form with every comment stripped. A sweep whose
#: results cannot be trusted is worse than no sweep.
_LOCK = ROOT / ".mutation-sweep.lock"


def _acquire_lock() -> bool:
    try:
        # O_EXCL: atomic, so two processes racing here cannot both win.
        fd = __import__("os").open(_LOCK, __import__("os").O_CREAT | __import__("os").O_EXCL | __import__("os").O_WRONLY)
    except FileExistsError:
        return False
    with __import__("os").fdopen(fd, "w") as handle:
        handle.write(str(__import__("os").getpid()))
    return True


def _release_lock() -> None:
    _LOCK.unlink(missing_ok=True)


def _verify_clean(paths: list[Path], originals: dict[Path, bytes]) -> list[str]:
    """Belt and braces: confirm every file really is byte-identical after the
    sweep. The `finally` restore should make this vacuous — but the whole
    lesson here is that a check which cannot fail proves nothing, so this one
    actually compares."""
    return [p.relative_to(ROOT).as_posix() for p in paths if p.read_bytes() != originals[p]]


def sweep(prefix: str, *, list_only: bool, basetemp: Path) -> int:
    targets = {src: tests for src, tests in TARGETS.items() if src.startswith(prefix)}
    if not targets:
        print(f"no targets matching {prefix!r}; known: {', '.join(sorted(TARGETS))}")
        return 2

    if not list_only and not _acquire_lock():
        print(f"another sweep holds {_LOCK.name}. Two sweeps mutating the same files "
              "produce results that describe neither of them. Delete the lock only if "
              "you are certain no sweep is running.")
        return 2

    total = survived = killed = skipped = 0
    survivors: list[str] = []
    originals: dict[Path, bytes] = {}

    for src, tests in sorted(targets.items()):
        path = ROOT / src
        if not path.exists():
            print(f"SKIP {src} (missing)")
            continue
        # BYTES, not text. `read_text` normalises CRLF to LF on the way in, so
        # restoring from a decoded string rewrites every line ending in the
        # file — the sweep would leave the whole tree modified even when it
        # changed nothing semantically. Round-tripping bytes is the only
        # restore that is genuinely a no-op.
        original_bytes = path.read_bytes()
        originals[path] = original_bytes
        original = original_bytes.decode("utf-8")
        candidates = mutants_for(path)
        print(f"\n{src}: {len(candidates)} mutants, tests={' '.join(tests)}")
        if list_only:
            total += len(candidates)
            continue

        # Restore on ANY exit path, including Ctrl-C and a hard crash.
        restore = lambda p=path, o=original_bytes: p.write_bytes(o)  # noqa: E731
        atexit.register(restore)
        try:
            for index, mutant in enumerate(candidates):
                mutated = _mutated_source(original, index)
                if mutated is None:
                    skipped += 1
                    continue
                total += 1
                path.write_bytes(mutated.encode("utf-8"))
                started = time.monotonic()
                try:
                    alive = _run_tests(tests, basetemp)
                except subprocess.TimeoutExpired:
                    # A mutant that hangs the suite is caught, not missed —
                    # an infinite loop is a failure like any other.
                    alive = False
                elapsed = time.monotonic() - started
                if alive:
                    survived += 1
                    survivors.append(mutant.label())
                    print(f"  SURVIVED  {mutant.label()}  ({elapsed:.0f}s)")
                else:
                    killed += 1
        finally:
            restore()
            atexit.unregister(restore)

    if list_only:
        print(f"\n{total} mutants across {len(targets)} files (nothing run)")
        return 0

    print(f"\n{'=' * 70}")
    print(f"killed {killed}  survived {survived}  unparseable {skipped}")
    if total:
        print(f"mutation score: {killed / total * 100:.1f}%")
    if survivors:
        print("\nSURVIVORS — each is a mutation the suite does not notice:")
        for line in survivors:
            print(f"  {line}")
    return 1 if survivors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", nargs="?", default="", help="only sweep targets starting with this path")
    parser.add_argument("--list", action="store_true", help="count mutants without running anything")
    parser.add_argument("--basetemp", default=None, help="pytest basetemp")
    args = parser.parse_args()

    basetemp = Path(args.basetemp) if args.basetemp else ROOT / ".mutation-tmp"
    return sweep(args.prefix, list_only=args.list, basetemp=basetemp)


if __name__ == "__main__":
    raise SystemExit(main())
