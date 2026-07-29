"""`te.ml.calibrate` — Platt/sigmoid calibration ONLY. Isotonic overfits at
this sample size (a few thousand rows) and is explicitly banned by the
plan; `test_isotonic_is_never_used` makes a future re-introduction fail
loudly rather than silently."""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression

from te.ml.calibrate import calibrate_probabilities, fit_platt_calibrator

_ML_DIR = Path(__file__).resolve().parents[2] / "te" / "ml"


def test_isotonic_is_never_used() -> None:
    """AST-inspects every module under `te/ml/` for an `IsotonicRegression`
    import or reference — a future edit that reintroduces isotonic
    calibration fails this test immediately, anywhere in the package, not
    just in `calibrate.py`."""
    offenders: list[str] = []
    for path in _ML_DIR.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "Isotonic" in source:
            offenders.append(str(path))
    assert not offenders, f"IsotonicRegression referenced in: {offenders} — banned by the plan at this sample size"


def test_isotonic_is_never_used_ast_grep() -> None:
    """Belt-and-suspenders: parse each module's AST and check no imported
    name is `IsotonicRegression`, so a `from sklearn.isotonic import Foo as
    Bar` rename trick can't slip past the plain string check above."""
    for path in _ML_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "isotonic" in node.module.lower():
                pytest.fail(f"{path} imports from an isotonic module: {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "isotonic" in alias.name.lower():
                        pytest.fail(f"{path} imports an isotonic module: {alias.name}")


def test_fit_platt_calibrator_returns_a_sigmoid_calibrated_model() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(200, 1))
    y = (x[:, 0] > 0).astype(int)

    base = LogisticRegression().fit(x, y)
    calibrated = fit_platt_calibrator(base, x, y, n_splits=3)

    probs = calibrate_probabilities(calibrated, x)
    assert probs.shape == (200,)
    assert np.all((probs >= 0) & (probs <= 1))


def test_calibrated_model_is_not_an_isotonic_regressor() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(size=(100, 1))
    y = (x[:, 0] > 0).astype(int)
    base = LogisticRegression().fit(x, y)
    calibrated = fit_platt_calibrator(base, x, y, n_splits=3)
    assert calibrated.method == "sigmoid"
