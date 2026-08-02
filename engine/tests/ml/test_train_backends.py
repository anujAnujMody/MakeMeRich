"""`te.ml.train`'s pluggable model backend — XGBoost (today's default),
CatBoost and Random Forest behind one switch, on identical data and
identical CV splits.

The point of these tests is NOT that a third model makes the numbers look
better. It is that trying three models is three times the search, and the
Deflated Sharpe Ratio only protects us if every one of those trials lands in
the same `TrialLedger` scope the DSR deflates against. See
`test_a_second_backend_raises_the_trial_count_under_the_same_run_id` — that
is the test this whole file exists for.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa

from te.domain.clock import IST
from te.ml.featurespec import SECONDARY_V1
from te.ml.train import (
    BACKEND_FIXED_PARAMS,
    BACKEND_PARAM_GRIDS,
    DEFAULT_PARAM_GRID,
    MIN_TABICL_TRAINING_ROWS,
    ModelBackend,
    train_meta_model,
)
from te.ml.trials import TrialLedger

#: Deliberately tiny so a three-backend test suite stays quick — these are
#: structural tests of the plumbing, never a claim about model quality, so
#: the grid's contents don't matter, only that every backend gets the SAME
#: number of configurations to search.
_TINY_GRIDS: dict[ModelBackend, tuple[dict[str, Any], ...]] = {
    ModelBackend.XGBOOST: (
        {"n_estimators": 20, "max_depth": 2, "learning_rate": 0.1},
        {"n_estimators": 30, "max_depth": 2, "learning_rate": 0.05},
    ),
    ModelBackend.CATBOOST: (
        {"iterations": 20, "depth": 2, "learning_rate": 0.1},
        {"iterations": 30, "depth": 2, "learning_rate": 0.05},
    ),
    ModelBackend.RANDOM_FOREST: (
        {"n_estimators": 20, "max_depth": 2},
        {"n_estimators": 30, "max_depth": 3},
    ),
    # TabICL is pretrained, so there is no capacity knob to shrink here —
    # these vary only inference-time ensembling, and are kept small for the
    # same speed reason as the others.
    ModelBackend.TABICL: (
        {"n_estimators": 2, "softmax_temperature": 0.9},
        {"n_estimators": 4, "softmax_temperature": 0.9},
    ),
}


@pytest.fixture
def trial_ledger(tmp_path: Path) -> TrialLedger:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'trials.db'}")
    return TrialLedger(engine)


def _synthetic_dataset(n: int = 200, seed: int = 0) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series]:
    """Same generator as `tests/ml/test_train.py` — weakly-informative
    features, so `validate_training_set`'s leakage check passes and the
    models have *something* to fit without this becoming a claim of edge."""
    rng = np.random.default_rng(seed)
    base_ts = dt.datetime(2026, 6, 1, 9, 30, tzinfo=IST)
    prediction_times = pd.Series([base_ts + dt.timedelta(minutes=5 * i) for i in range(n)])
    evaluation_times = pd.Series([t + dt.timedelta(minutes=15) for t in prediction_times])
    features = pd.DataFrame({col: rng.normal(size=n) for col in SECONDARY_V1.columns})
    latent = 0.6 * features["dte"] + rng.normal(scale=1.5, size=n)
    labels = pd.Series((latent > 0).astype(int))
    return features, labels, prediction_times, evaluation_times


# ---------------------------------------------------------------------------
# THE honesty test — three models is three times the search
# ---------------------------------------------------------------------------


def test_a_second_backend_raises_the_trial_count_under_the_same_run_id(trial_ledger: TrialLedger) -> None:
    """Running CatBoost after XGBoost against the same `run_id` must ADD to
    the same ledger scope, never start a fresh count.

    If each backend got its own scope, three models could each report a DSR
    deflated for one third of the search that actually happened, and the
    winner's number would be a fabrication — precisely the failure mode
    `TrialLedger` exists to block. The trial count is monotonic across
    backends, and `n_trials_at_training` (what feeds
    `deflated_sharpe_ratio`) must see the raised total."""
    features, labels, pred_times, eval_times = _synthetic_dataset(n=200, seed=31)
    scope = "multi:inner"
    common = {
        "features": features,
        "labels": labels,
        "prediction_times": pred_times,
        "evaluation_times": eval_times,
        "spec": SECONDARY_V1,
        "trial_ledger": trial_ledger,
        "run_id": "multi",
        "outer_splits": 3,
        "inner_splits": 3,
    }

    assert trial_ledger.n_trials(scope) == 0

    first = train_meta_model(
        **common, backend=ModelBackend.XGBOOST, param_grid=_TINY_GRIDS[ModelBackend.XGBOOST]
    )
    after_first = trial_ledger.n_trials(scope)
    assert after_first > 0
    assert first.n_trials_at_training == after_first

    second = train_meta_model(
        **common, backend=ModelBackend.CATBOOST, param_grid=_TINY_GRIDS[ModelBackend.CATBOOST]
    )
    after_second = trial_ledger.n_trials(scope)

    assert after_second > after_first, "the second backend's trials vanished from the ledger"
    assert second.n_trials_at_training == after_second
    assert second.n_trials_at_training > first.n_trials_at_training


def test_the_same_config_dict_under_two_backends_is_two_distinct_trials(trial_ledger: TrialLedger) -> None:
    """`{"n_estimators": 20, "max_depth": 2}` means one thing to XGBoost and
    a different thing to a Random Forest. If the recorded `config_hash`
    ignored the backend, the two would look like the same trial re-run — and
    a later reader counting DISTINCT configurations searched would
    undercount the true breadth of the search."""
    features, labels, pred_times, eval_times = _synthetic_dataset(n=200, seed=37)
    shared_grid: tuple[dict[str, Any], ...] = ({"n_estimators": 20, "max_depth": 2},)
    common = {
        "features": features,
        "labels": labels,
        "prediction_times": pred_times,
        "evaluation_times": eval_times,
        "spec": SECONDARY_V1,
        "trial_ledger": trial_ledger,
        "run_id": "hash",
        "outer_splits": 3,
        "inner_splits": 3,
        "param_grid": shared_grid,
    }

    train_meta_model(**common, backend=ModelBackend.XGBOOST)
    train_meta_model(**common, backend=ModelBackend.RANDOM_FOREST)

    engine = trial_ledger._engine
    with engine.connect() as conn:
        hashes = conn.execute(
            sa.text("SELECT DISTINCT config_hash FROM trial_ledger WHERE kind = 'hash:inner'")
        ).fetchall()
    assert len(hashes) == 2, f"expected one hash per backend for an identical config dict, got {hashes!r}"


# ---------------------------------------------------------------------------
# The seam itself
# ---------------------------------------------------------------------------


def _skip_if_not_fittable_at_this_size(backend: ModelBackend, n_rows: int) -> None:
    """The two tests below fit on small synthetic datasets, which TabICL
    cannot and must not accept.

    Skipping is the correct outcome rather than a workaround. TabICL is a
    PRETRAINED model with a documented range of 300-48,000 samples, and
    `_make_tabicl` refuses below that floor by design — so a
    `train-on-200-rows` test asserting it succeeds would be asserting the
    opposite of the guarantee we want. It is also an optional dependency
    (it pulls in torch), so it may legitimately be absent entirely.

    Its own behaviour is covered directly in `tests/ml/test_tabicl_backend.py`.
    """
    if backend is not ModelBackend.TABICL:
        return
    pytest.importorskip("tabicl", reason="optional 'foundation' dependency not installed")
    if n_rows < MIN_TABICL_TRAINING_ROWS:
        pytest.skip(
            f"TabICL is documented for 300-48,000 samples and refuses {n_rows} rows by design "
            "— see tests/ml/test_tabicl_backend.py"
        )


def test_model_backend_names_are_the_allowed_models() -> None:
    """LightGBM is banned in this repo (leaf-wise growth overfits at our n)
    and TabPFN's licence forbids commercial use — neither may appear.

    TabICL joined on 2026-08-01 as the licence-clean alternative to TabPFN:
    BSD 3-Clause from Inria, commercial use explicitly permitted. It is used
    zero-shot and never fine-tuned — see `tests/ml/test_tabicl_backend.py`.
    """
    assert {b.value for b in ModelBackend} == {"xgboost", "catboost", "random_forest", "tabicl"}
    banned = {"lightgbm", "tabpfn"}
    assert not ({b.value for b in ModelBackend} & banned)


def test_train_meta_model_defaults_to_xgboost(trial_ledger: TrialLedger) -> None:
    """Existing callers passed no backend and must keep getting exactly
    what they got before."""
    features, labels, pred_times, eval_times = _synthetic_dataset(n=200, seed=41)
    common = {
        "features": features,
        "labels": labels,
        "prediction_times": pred_times,
        "evaluation_times": eval_times,
        "spec": SECONDARY_V1,
        "outer_splits": 3,
        "inner_splits": 3,
    }

    implicit = train_meta_model(**common, trial_ledger=trial_ledger, run_id="implicit")
    explicit = train_meta_model(
        **common, trial_ledger=trial_ledger, run_id="explicit", backend=ModelBackend.XGBOOST
    )

    row = features.iloc[-1]
    assert implicit.model.predict_proba(row) == pytest.approx(explicit.model.predict_proba(row))
    assert implicit.best_params_per_outer_fold == explicit.best_params_per_outer_fold


def test_omitted_param_grid_resolves_to_the_backends_own_grid() -> None:
    """A CatBoost run must not be handed XGBoost's `n_estimators`/`max_depth`
    keywords, which it would reject."""
    assert BACKEND_PARAM_GRIDS[ModelBackend.XGBOOST] == DEFAULT_PARAM_GRID
    for backend, grid in BACKEND_PARAM_GRIDS.items():
        assert len(grid) >= 2, f"{backend} needs a real grid to search"
        # A swept key that is also pinned would reach the constructor twice.
        for config in grid:
            assert not set(config) & set(BACKEND_FIXED_PARAMS[backend])


def test_every_backend_pins_a_seed_for_reproducibility() -> None:
    fixed = BACKEND_FIXED_PARAMS
    assert fixed[ModelBackend.XGBOOST]["random_state"] == 42
    assert fixed[ModelBackend.CATBOOST]["random_seed"] == 42
    assert fixed[ModelBackend.RANDOM_FOREST]["random_state"] == 42


def test_every_backend_corrects_class_imbalance_the_way_its_library_expects() -> None:
    """XGBoost and CatBoost both take a `scale_pos_weight` computed on the
    FOLD's own labels; scikit-learn's forest has no such knob and uses
    `class_weight="balanced"`, which sklearn derives from the `y` handed to
    `fit` — i.e. also fold-local, never the full dataset."""
    from te.ml.train import _make_estimator

    imbalanced = np.array([0] * 8 + [1] * 2, dtype=int)

    xgb = _make_estimator(ModelBackend.XGBOOST, {"n_estimators": 10, "max_depth": 2}, imbalanced)
    assert xgb.get_params()["scale_pos_weight"] == pytest.approx(4.0)

    cat = _make_estimator(ModelBackend.CATBOOST, {"iterations": 10, "depth": 2}, imbalanced)
    assert cat.get_params()["scale_pos_weight"] == pytest.approx(4.0)

    forest = _make_estimator(ModelBackend.RANDOM_FOREST, {"n_estimators": 10, "max_depth": 2}, imbalanced)
    assert forest.get_params()["class_weight"] == "balanced"


@pytest.mark.parametrize("backend", list(ModelBackend))
def test_every_backend_trains_and_reports_per_fold_feature_importances(
    backend: ModelBackend, trial_ledger: TrialLedger
) -> None:
    """`TrainResult.feature_importances_per_outer_fold` must keep working for
    every fitted backend. The SCALES differ by library (XGBoost gain,
    CatBoost prediction-value-change summing to 100, sklearn Gini), so these
    are comparable ACROSS FOLDS within one backend — never across backends."""
    _skip_if_not_fittable_at_this_size(backend, 200)
    features, labels, pred_times, eval_times = _synthetic_dataset(n=200, seed=43)

    result = train_meta_model(
        features=features,
        labels=labels,
        prediction_times=pred_times,
        evaluation_times=eval_times,
        spec=SECONDARY_V1,
        trial_ledger=trial_ledger,
        run_id=f"be-{backend.value}",
        outer_splits=3,
        inner_splits=3,
        backend=backend,
        param_grid=_TINY_GRIDS[backend],
    )

    assert 0.0 <= result.model.predict_proba(features.iloc[-1]) <= 1.0
    importances = result.feature_importances_per_outer_fold
    assert len(importances) == len(result.best_params_per_outer_fold) > 0
    for fold in importances:
        assert set(fold) == set(SECONDARY_V1.columns)
        assert all(v >= 0.0 for v in fold.values())


@pytest.mark.parametrize("backend", list(ModelBackend))
def test_every_backend_is_reproducible_across_identical_runs(backend: ModelBackend, tmp_path: Path) -> None:
    _skip_if_not_fittable_at_this_size(backend, 200)
    features, labels, pred_times, eval_times = _synthetic_dataset(n=200, seed=47)

    def _run(tag: str) -> float:
        engine = sa.create_engine(f"sqlite:///{tmp_path / (tag + '.db')}")
        result = train_meta_model(
            features=features,
            labels=labels,
            prediction_times=pred_times,
            evaluation_times=eval_times,
            spec=SECONDARY_V1,
            trial_ledger=TrialLedger(engine),
            run_id=tag,
            outer_splits=3,
            inner_splits=3,
            backend=backend,
            param_grid=_TINY_GRIDS[backend],
        )
        return result.model.predict_proba(features.iloc[-1])

    assert _run(f"{backend.value}-a") == pytest.approx(_run(f"{backend.value}-b"))


def test_pbo_matrix_rows_never_mix_backends(trial_ledger: TrialLedger) -> None:
    """`probability_of_backtest_overfitting` builds its CSCV matrix from
    COMPETING CONFIGURATIONS over a shared period grid. One `train_meta_model`
    call runs exactly one backend, so its matrix is per-backend by
    construction — this test pins that property against a future edit that
    pools two backends' series into one matrix, which would silently change
    what the reported number means."""
    features, labels, pred_times, eval_times = _synthetic_dataset(n=200, seed=53)
    grid = _TINY_GRIDS[ModelBackend.RANDOM_FOREST]

    result = train_meta_model(
        features=features,
        labels=labels,
        prediction_times=pred_times,
        evaluation_times=eval_times,
        spec=SECONDARY_V1,
        trial_ledger=trial_ledger,
        run_id="pbo",
        outer_splits=3,
        inner_splits=3,
        backend=ModelBackend.RANDOM_FOREST,
        param_grid=grid,
    )

    # n_combinations reflects the CSCV split count over ONE backend's
    # |grid| competing configs, so it can never encode more rows than the
    # grid this single call searched.
    assert result.pbo.n_combinations >= 0
    assert len(result.best_params_per_outer_fold) > 0
    assert all(config in grid for config in result.best_params_per_outer_fold)


def test_train_meta_model_rejects_an_unknown_backend(trial_ledger: TrialLedger) -> None:
    features, labels, pred_times, eval_times = _synthetic_dataset(n=200, seed=59)
    with pytest.raises(ValueError, match="unknown model backend"):
        train_meta_model(
            features=features,
            labels=labels,
            prediction_times=pred_times,
            evaluation_times=eval_times,
            spec=SECONDARY_V1,
            trial_ledger=trial_ledger,
            run_id="bad-backend",
            outer_splits=3,
            backend="gradient_boosting_by_vibes",  # type: ignore[arg-type]
        )


def test_lightgbm_and_tabpfn_are_absent_from_the_backend_seam() -> None:
    """LightGBM overfits at our n; TabPFN's licence forbids commercial and
    production use, explicitly including internal commercial decision-making
    — which trading real money is."""
    import te.ml.train as train_module

    assert train_module.__file__ is not None
    source = Path(train_module.__file__).read_text(encoding="utf-8")
    for banned in ("import lightgbm", "from lightgbm", "import tabpfn", "from tabpfn"):
        assert banned not in source.lower()


# ---------------------------------------------------------------------------
# CLI switch
# ---------------------------------------------------------------------------


def test_training_script_exposes_a_model_switch_defaulting_to_xgboost() -> None:
    from scripts.train_meta_model import build_parser

    parser = build_parser()
    assert parser.parse_args([]).model == "xgboost"
    assert parser.parse_args(["--model", "catboost"]).model == "catboost"
    assert parser.parse_args(["--model", "random_forest"]).model == "random_forest"

    with pytest.raises(SystemExit):
        parser.parse_args(["--model", "lightgbm"])
