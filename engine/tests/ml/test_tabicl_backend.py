"""TabICL is a PRETRAINED model, and the two ways that makes it different
from the other three backends are load-bearing enough to pin here.

Both properties are invisible at the call site — an out-of-range TabICL, or
a fine-tuned one, still returns perfectly well-formed probabilities. That is
exactly why they need tests: nothing downstream (calibration, DSR, PBO, the
maturity gate) could tell you the number came from outside the model's
documented range.

Sources for the numbers pinned below, verified 2026-08-01:
- soda-inria/tabicl states TabICL is pretrained on datasets of 300-48,000
  samples and that the authors "have not tested if TabICL generalizes to
  datasets smaller than 300 samples".
- Independent benchmarking (Mission Lane, enterprise credit risk) found
  fine-tuning it on small data collapses accuracy from 0.873 to 0.567 on
  TabZilla — the reason this project uses it zero-shot only.

These tests deliberately do NOT require `tabicl` to be installed: it is an
optional dependency (it pulls in torch, ~2GB), and the guarantees above are
about OUR code, not about theirs.
"""

from __future__ import annotations

import builtins
import sys
from typing import Any

import numpy as np
import pytest

from te.ml.train import (
    BACKEND_FIXED_PARAMS,
    BACKEND_PARAM_GRIDS,
    MIN_TABICL_TRAINING_ROWS,
    ModelBackend,
    TabIclDatasetTooSmallError,
    TabIclUnavailableError,
    _make_estimator,
)


class _FakeTabIcl:
    """Stands in for `TabICLClassifier`, recording what it was constructed
    with so the test can assert on the arguments rather than on a network
    download of the real pretrained checkpoint."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


@pytest.fixture
def fake_tabicl(monkeypatch: pytest.MonkeyPatch) -> type[_FakeTabIcl]:
    """Installs a fake `tabicl` module, because `_make_tabicl` imports it
    lazily from inside the function body."""
    module = type(sys)("tabicl")
    module.TabICLClassifier = _FakeTabIcl  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tabicl", module)
    return _FakeTabIcl


def _labels(n: int) -> np.ndarray:
    """A balanced-ish label vector of length `n` — only its LENGTH matters to
    the size guard, but a degenerate single-class vector would be a
    misleading fixture."""
    return np.array([i % 2 for i in range(n)], dtype=int)


def test_refuses_a_fold_smaller_than_the_authors_tested(fake_tabicl: type[_FakeTabIcl]) -> None:
    """The whole point. Below 300 rows the model is outside its documented
    range, and a silent prediction there is an unearned number."""
    del fake_tabicl
    with pytest.raises(TabIclDatasetTooSmallError, match="untested below 300"):
        _make_estimator(ModelBackend.TABICL, {}, _labels(MIN_TABICL_TRAINING_ROWS - 1))


def test_accepts_a_fold_at_the_documented_floor(fake_tabicl: type[_FakeTabIcl]) -> None:
    """The boundary is inclusive — 300 is inside the authors' stated range,
    so refusing it would be our own invention rather than their limit."""
    estimator = _make_estimator(ModelBackend.TABICL, {}, _labels(MIN_TABICL_TRAINING_ROWS))
    assert isinstance(estimator, fake_tabicl)


def test_the_guard_is_checked_against_the_fold_not_the_whole_dataset() -> None:
    """`_make_estimator` only ever sees one fold's training labels, which is
    the point: a 2,600-row dataset can still produce inner folds below the
    floor once purging and embargoing have removed rows, and it is the fold
    that the predictions actually come from.

    Pinned as a test because the tempting 'validate the dataset once at the
    door' refactor would silently reintroduce exactly this hole.
    """
    import inspect

    from te.ml.train import _make_tabicl

    source = inspect.getsource(_make_tabicl)
    assert "len(y_train)" in source, "the size guard must read the fold's own labels"


def test_never_fine_tunes(fake_tabicl: type[_FakeTabIcl]) -> None:
    """No fine-tuning switch may be passed, because fine-tuning TabICL on
    data this small is documented to make it dramatically worse. `fit()` on
    a TabICL classifier stores in-context examples; the transformer weights
    stay frozen, and nothing here may change that."""
    estimator = _make_estimator(ModelBackend.TABICL, {}, _labels(500))
    assert isinstance(estimator, fake_tabicl)
    forbidden = {"fine_tune", "finetune", "n_epochs", "epochs", "lr", "learning_rate", "train"}
    passed = set(estimator.kwargs)
    assert not (passed & forbidden), f"a fine-tuning argument reached TabICL: {passed & forbidden}"


def test_a_missing_optional_dependency_says_so_precisely(monkeypatch: pytest.MonkeyPatch) -> None:
    """`tabicl` is optional. A bare ImportError deep in a training run reads
    like a bug in our code; this must name the install command instead."""
    monkeypatch.delitem(sys.modules, "tabicl", raising=False)
    real_import = builtins.__import__

    def _refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "tabicl":
            raise ImportError("No module named 'tabicl'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _refuse)
    with pytest.raises(TabIclUnavailableError, match="foundation"):
        _make_estimator(ModelBackend.TABICL, {}, _labels(500))


def test_its_search_is_no_wider_than_any_other_backend() -> None:
    """Every grid is the same size on purpose (see `CATBOOST_PARAM_GRID`'s
    note): a wider search buys a better-looking number for free, so comparing
    backends with unequal grids would rank search breadth, not model
    quality."""
    sizes = {backend: len(grid) for backend, grid in BACKEND_PARAM_GRIDS.items()}
    assert len(set(sizes.values())) == 1, f"backend grids differ in size: {sizes}"


def test_it_is_registered_everywhere_the_other_backends_are() -> None:
    """A backend present in the enum but missing from a lookup table fails
    only at run time, halfway through a training run."""
    assert ModelBackend.TABICL in BACKEND_PARAM_GRIDS
    assert ModelBackend.TABICL in BACKEND_FIXED_PARAMS


def test_the_fixed_params_pin_reproducibility_and_zero_shot_use() -> None:
    fixed = BACKEND_FIXED_PARAMS[ModelBackend.TABICL]
    assert fixed["random_state"] == 42, "must match the other backends' reproducibility contract"
    assert "allow_auto_download" in fixed, "the checkpoint download must be an explicit, visible choice"
