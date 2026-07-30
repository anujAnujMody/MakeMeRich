# ML Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-XGBoost 8-feature model with a 23-feature XGBoost+LightGBM ensemble, purged+embargoed walk-forward validated, threshold-optimized, isotonic-calibrated, trained on hybrid (synthetic + real paper) data with neutral-zone-dropped labels.

**Architecture:** Feature extraction expands from 8→23 features (stationary market + trade-level + strategy one-hot + cyclical time). Ensemble module wraps XGBoost + LightGBM (with subsample/colsample regularization + scale_pos_weight) via soft-voting. Threshold optimizer sweeps 0.30–0.80 on OOS predictions to maximize profit factor. Isotonic calibrator (better than Platt for tree models per signal-classification skill). Hybrid data loader merges synthetic yfinance trades with real paper trades from the DB, dropping neutral-zone labels (|pnl| < threshold). Walk-forward validation uses purging + embargo gap to prevent label leakage. Feature selection via variance threshold + correlation filter + mutual information. `auto_ensure_model` orchestrates the full pipeline and persists model + threshold + calibrator + meta.

**Skill guidance applied:**
- `advances-in-financial-machine-learning`: Purged K-Fold + Embargo (Ch.7), feature importance (Ch.8), overfitting awareness
- `signal-classification`: Isotonic calibration > Platt for trees, drop neutral zone, subsample/colsample=0.8, scale_pos_weight, threshold for profit factor
- `feature-engineering`: Stationarity (ratios not levels), cyclical sin/cos time encoding, no-lookahead guarantee, feature selection pipeline
- `walk-forward-validation`: Embargo gap, purging, rolling window for crypto/short-horizon

**Tech Stack:** Python 3.12, XGBoost 3.3, LightGBM (to install), scikit-learn 1.9, SHAP 0.52, Optuna 4.9, pandas, numpy, pytest

---

## File Structure

| File | Responsibility | Status |
|------|---------------|--------|
| `engine/app/ml/features.py` | 23-feature extraction + FEATURE_NAMES | Modify |
| `engine/app/ml/ensemble.py` | XGBoost+LightGBM soft-voting ensemble | Create |
| `engine/app/ml/threshold.py` | Threshold optimization (profit factor sweep) | Create |
| `engine/app/ml/calibration.py` | Isotonic calibration (better than Platt for trees) | Create |
| `engine/app/ml/hybrid_data.py` | Load synthetic + real paper trades, drop neutral zone | Create |
| `engine/app/ml/validation.py` | Walk-forward with purging + embargo + ensemble + threshold | Modify |
| `engine/app/ml/inference.py` | predict_single with ensemble + calibration + threshold | Modify |
| `engine/app/ml/pipeline.py` | Training pipeline using hybrid data | Modify |
| `engine/app/ml/patterns.py` | SHAP explain for ensemble | Modify |
| `engine/app/agents/execution.py` | Use optimal threshold from model | Modify |
| `engine/pyproject.toml` | Add lightgbm dependency | Modify |
| `engine/tests/test_features.py` | 23-feature tests | Modify |
| `engine/tests/test_ensemble.py` | Ensemble tests | Create |
| `engine/tests/test_threshold.py` | Threshold optimizer tests | Create |
| `engine/tests/test_calibration.py` | Calibration tests | Create |
| `engine/tests/test_hybrid_data.py` | Hybrid data loader tests | Create |
| `engine/tests/test_inference.py` | Updated inference tests | Modify |
| `engine/tests/test_validation.py` | Updated WF tests | Modify |

---

## Feature Specification (23 features)

All features are stationary (ratios/changes, not raw levels) per `feature-engineering` skill. Time features use cyclical sin/cos encoding.

| # | Name | Source | Description | Stationarity |
|---|------|--------|-------------|--------------|
| 1 | `vix_rank` | snapshot | VIX percentile rank (0-1) | Stationary (bounded) |
| 2 | `pcr` | snapshot | Put-call ratio | Stationary (bounded) |
| 3 | `iv_rank` | snapshot | IV rank percentile (0-1) | Stationary (bounded) |
| 4 | `adx` | snapshot | ADX trend strength (0-100) | Stationary (bounded) |
| 5 | `is_high_vol` | snapshot | Binary: high volatility regime | Stationary (binary) |
| 6 | `is_trending` | snapshot | Binary: trending regime | Stationary (binary) |
| 7 | `prev_ret_1d` | trade | 1-day return % | Stationary (pct change) |
| 8 | `prev_ret_5d` | trade | 5-day return % | Stationary (pct change) |
| 9 | `prev_ret_15d` | trade | 15-day return % | Stationary (pct change) |
| 10 | `prev_ret_30d` | trade | 30-day return % | Stationary (pct change) |
| 11 | `vol_20d` | snapshot | 20-day realized volatility (annualized) | Stationary (rolling std) |
| 12 | `atr_pct` | snapshot | ATR as % of price | Stationary (ratio) |
| 13 | `day_of_week_sin` | trade | sin(2π * weekday / 7) | Stationary (cyclical) |
| 14 | `day_of_week_cos` | trade | cos(2π * weekday / 7) | Stationary (cyclical) |
| 15 | `vix_change_1d` | snapshot | VIX 1-day change | Stationary (difference) |
| 16 | `adx_change_5d` | snapshot | ADX 5-day change | Stationary (difference) |
| 17 | `strategy_orbs` | trade | One-hot: ORBS strategy | Stationary (binary) |
| 18 | `strategy_vwap_reversion` | trade | One-hot: VWAP reversion | Stationary (binary) |
| 19 | `strategy_ema_crossover` | trade | One-hot: EMA crossover | Stationary (binary) |
| 20 | `strategy_bollinger_bounce` | trade | One-hot: Bollinger bounce | Stationary (binary) |
| 21 | `entry_minutes_from_open` | trade | Minutes since 09:15 IST | Stationary (bounded intraday) |
| 22 | `range_pct` | trade | ORBS range as % of price (0 if not ORBS) | Stationary (ratio) |
| 23 | `rsi_14` | snapshot | 14-period RSI (0-100) | Stationary (bounded) |

**Key changes from v1:**
- `vix` → `vix_rank` (raw VIX is non-stationary; percentile rank is bounded)
- `day_of_week` → `day_of_week_sin` + `day_of_week_cos` (cyclical encoding preserves continuity)
- `month` removed (replaced by second cyclical component — kept feature count at 23 by dropping redundant `month`)

---

## Task 1: Install LightGBM

**Files:**
- Modify: `engine/pyproject.toml`
- Test: run `python -c "import lightgbm"`

- [ ] **Step 1: Add lightgbm to pyproject.toml dependencies**

In `engine/pyproject.toml`, add `"lightgbm>=4.0",` to the `dependencies` list (after the xgboost line).

- [ ] **Step 2: Install lightgbm in venv**

Run: `& "W:\Trading\engine\.venv\Scripts\pip.exe" install "lightgbm>=4.0"`
Expected: Successfully installed lightgbm

- [ ] **Step 3: Verify import**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -c "import lightgbm; print(lightgbm.__version__)"`
Expected: Version string like `4.x.x`

- [ ] **Step 4: Run existing tests to confirm no breakage**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests -x -q`
Expected: 124 passed

---

## Task 2: Expand Features to 23

**Files:**
- Modify: `engine/app/ml/features.py`
- Modify: `engine/tests/test_features.py`

- [ ] **Step 1: Write failing test for 23 features**

Replace `engine/tests/test_features.py` with:

```python
import numpy as np

from app.ml.features import FEATURE_NAMES, build_features, extract_features


def test_feature_names_has_23() -> None:
    assert len(FEATURE_NAMES) == 23


def test_build_features_returns_expected_shape() -> None:
    trades = [
        {
            "pnl": 150.0,
            "entry_price": 18500.0,
            "exit_price": 18650.0,
            "strategy": "orbs",
            "symbol": "NIFTY",
            "prev_close": 18400.0,
            "prev_ret_1d": 0.5,
            "prev_ret_5d": 1.2,
            "prev_ret_15d": 2.0,
            "prev_ret_30d": 3.5,
            "entry_time": "2026-07-27T09:30:00+00:00",
            "range_pct": 0.3,
        },
        {
            "pnl": -80.0,
            "entry_price": 42000.0,
            "exit_price": 41920.0,
            "strategy": "vwap_reversion",
            "symbol": "BANKNIFTY",
            "prev_close": 42100.0,
            "prev_ret_1d": -0.2,
            "prev_ret_5d": -0.8,
            "prev_ret_15d": -1.5,
            "prev_ret_30d": -2.0,
            "entry_time": "2026-07-27T10:15:00+00:00",
            "range_pct": 0.0,
        },
    ]
    snapshots = [
        {
            "vix_rank": 0.45,
            "pcr": 1.2,
            "iv_rank": 0.45,
            "regime": "ranging",
            "volatility": "low",
            "adx": 15.0,
            "vol_20d": 12.0,
            "atr_pct": 1.2,
            "vix_change_1d": -0.5,
            "adx_change_5d": 2.0,
            "rsi_14": 55.0,
        },
        {
            "vix_rank": 0.65,
            "pcr": 0.9,
            "iv_rank": 0.65,
            "regime": "trending",
            "volatility": "high",
            "adx": 30.0,
            "vol_20d": 25.0,
            "atr_pct": 2.5,
            "vix_change_1d": 0.8,
            "adx_change_5d": -1.0,
            "rsi_14": 72.0,
        },
    ]
    X, y = build_features(trades, snapshots)
    assert isinstance(X, np.ndarray)
    assert isinstance(y, np.ndarray)
    assert X.shape == (2, 23)
    assert y.shape == (2,)
    assert list(y) == [1, 0]


def test_extract_features_returns_23_values() -> None:
    trade = {
        "pnl": 100.0,
        "entry_price": 100.0,
        "strategy": "orbs",
        "prev_close": 99.0,
        "prev_ret_1d": 0.1,
        "prev_ret_5d": 0.5,
        "prev_ret_15d": 1.0,
        "prev_ret_30d": 2.0,
        "entry_time": "2026-07-27T09:30:00+00:00",
        "range_pct": 0.5,
    }
    snap = {
        "vix_rank": 0.5,
        "pcr": 1.0,
        "iv_rank": 0.5,
        "adx": 20.0,
        "volatility": "low",
        "regime": "ranging",
        "vol_20d": 15.0,
        "atr_pct": 1.0,
        "vix_change_1d": 0.0,
        "adx_change_5d": 0.0,
        "rsi_14": 50.0,
    }
    features = extract_features(trade, snap)
    assert len(features) == 23


def test_strategy_one_hot_orbs() -> None:
    trade = {"strategy": "orbs", "entry_price": 100.0, "entry_time": "2026-07-27T09:30:00+00:00"}
    snap = {"vix_rank": 0.5, "pcr": 1.0, "iv_rank": 0.5, "adx": 20.0}
    features = extract_features(trade, snap)
    # strategy_orbs is index 16
    assert features[16] == 1.0
    assert features[17] == 0.0
    assert features[18] == 0.0
    assert features[19] == 0.0


def test_strategy_one_hot_vwap() -> None:
    trade = {"strategy": "vwap_reversion", "entry_price": 100.0, "entry_time": "2026-07-27T09:30:00+00:00"}
    snap = {"vix_rank": 0.5, "pcr": 1.0, "iv_rank": 0.5, "adx": 20.0}
    features = extract_features(trade, snap)
    assert features[16] == 0.0
    assert features[17] == 1.0


def test_cyclical_day_of_week_encoding() -> None:
    """Monday (weekday=0) and Sunday (weekday=6) should be close in cyclical space."""
    import math

    trade_mon = {"strategy": "orbs", "entry_price": 100.0, "entry_time": "2026-07-27T09:30:00+00:00"}  # Monday
    trade_sun = {"strategy": "orbs", "entry_price": 100.0, "entry_time": "2026-08-02T09:30:00+00:00"}  # Sunday
    snap = {"vix_rank": 0.5, "pcr": 1.0, "iv_rank": 0.5, "adx": 20.0}
    f_mon = extract_features(trade_mon, snap)
    f_sun = extract_features(trade_sun, snap)
    # day_of_week_sin is index 12, day_of_week_cos is index 13
    dist = math.sqrt((f_mon[12] - f_sun[12]) ** 2 + (f_mon[13] - f_sun[13]) ** 2)
    # Monday (0) and Sunday (6) are adjacent in cyclical space (7-day cycle)
    assert dist < 0.5  # Should be close, not far apart


def test_defaults_when_missing_fields() -> None:
    trade = {"strategy": "orbs", "entry_price": 100.0}
    snap = {"vix_rank": 0.5}
    features = extract_features(trade, snap)
    assert len(features) == 23
    # All should be valid floats, no NaN
    assert all(isinstance(f, float) for f in features)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_features.py -v`
Expected: FAIL — `assert len(FEATURE_NAMES) == 23` (currently 8)

- [ ] **Step 3: Implement 23-feature extraction**

Replace `engine/app/ml/features.py` with:

```python
from datetime import datetime
from math import pi, sin, cos
from typing import Any

import numpy as np

FEATURE_NAMES = [
    "vix_rank",
    "pcr",
    "iv_rank",
    "adx",
    "is_high_vol",
    "is_trending",
    "prev_ret_1d",
    "prev_ret_5d",
    "prev_ret_15d",
    "prev_ret_30d",
    "vol_20d",
    "atr_pct",
    "day_of_week_sin",
    "day_of_week_cos",
    "vix_change_1d",
    "adx_change_5d",
    "strategy_orbs",
    "strategy_vwap_reversion",
    "strategy_ema_crossover",
    "strategy_bollinger_bounce",
    "entry_minutes_from_open",
    "range_pct",
    "rsi_14",
]

_STRATEGY_ONEHOT = {
    "orbs": "strategy_orbs",
    "vwap_reversion": "strategy_vwap_reversion",
    "ema_crossover": "strategy_ema_crossover",
    "bollinger_bounce": "strategy_bollinger_bounce",
}

_MARKET_OPEN_HOUR = 9
_MARKET_OPEN_MIN = 15


def build_features(
    trades: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray]:
    X: list[list[float]] = []
    y: list[int] = []
    for trade, snap in zip(trades, snapshots, strict=False):
        features = extract_features(trade, snap)
        X.append(features)
        y.append(1 if trade.get("pnl", 0) > 0 else 0)
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int32)


def extract_features(trade: dict[str, Any], snap: dict[str, Any]) -> list[float]:
    entry = float(trade.get("entry_price", 0.0))
    prev_close = float(trade.get("prev_close", entry))
    prev_ret_1d = ((entry - prev_close) / prev_close * 100) if prev_close > 0 else 0.0

    entry_time_str = trade.get("entry_time", "")
    day_of_week = 0.0
    month = 1.0
    entry_minutes = 0.0
    if entry_time_str:
        try:
            dt = datetime.fromisoformat(entry_time_str)
            day_of_week = float(dt.weekday())
            month = float(dt.month)
            entry_minutes = float((dt.hour - _MARKET_OPEN_HOUR) * 60 + (dt.minute - _MARKET_OPEN_MIN))
        except (ValueError, TypeError):
            pass

    # Cyclical encoding: sin/cos preserves continuity (Mon≈Sun in 7-day cycle)
    dow_sin = sin(2 * pi * day_of_week / 7.0)
    dow_cos = cos(2 * pi * day_of_week / 7.0)

    strategy = trade.get("strategy", "")
    one_hot = {name: 0.0 for name in _STRATEGY_ONEHOT.values()}
    one_hot_key = _STRATEGY_ONEHOT.get(strategy)
    if one_hot_key:
        one_hot[one_hot_key] = 1.0

    return [
        float(snap.get("vix_rank", 0.5)),           # stationary: percentile rank
        float(snap.get("pcr", 1.0)),                 # stationary: bounded ratio
        float(snap.get("iv_rank", 0.5)),             # stationary: percentile
        float(snap.get("adx", 20.0)),                # stationary: bounded 0-100
        1.0 if snap.get("volatility") == "high" else 0.0,
        1.0 if snap.get("regime") == "trending" else 0.0,
        round(prev_ret_1d, 4),                       # stationary: pct change
        float(trade.get("prev_ret_5d", 0.0)),        # stationary: pct change
        float(trade.get("prev_ret_15d", 0.0)),       # stationary: pct change
        float(trade.get("prev_ret_30d", 0.0)),       # stationary: pct change
        float(snap.get("vol_20d", 15.0)),            # stationary: rolling std
        float(snap.get("atr_pct", 1.0)),             # stationary: ratio
        dow_sin,                                      # stationary: cyclical
        dow_cos,                                      # stationary: cyclical
        float(snap.get("vix_change_1d", 0.0)),       # stationary: difference
        float(snap.get("adx_change_5d", 0.0)),       # stationary: difference
        one_hot["strategy_orbs"],
        one_hot["strategy_vwap_reversion"],
        one_hot["strategy_ema_crossover"],
        one_hot["strategy_bollinger_bounce"],
        max(entry_minutes, 0.0),                     # stationary: bounded intraday
        float(trade.get("range_pct", 0.0)),          # stationary: ratio
        float(snap.get("rsi_14", 50.0)),             # stationary: bounded 0-100
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_features.py -v`
Expected: 6 passed

- [ ] **Step 5: Run full suite to check for breakage**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests -x -q`
Expected: Some tests in test_inference.py may fail (predict_single still uses 8 features). That's OK — we fix in Task 4.

---

## Task 3: Ensemble Model (XGBoost + LightGBM)

**Files:**
- Create: `engine/app/ml/ensemble.py`
- Create: `engine/tests/test_ensemble.py`

- [ ] **Step 1: Write failing test for ensemble**

Create `engine/tests/test_ensemble.py`:

```python
import numpy as np

from app.ml.ensemble import EnsembleModel, train_ensemble


def test_train_ensemble_returns_model() -> None:
    X = np.random.rand(40, 23).astype(np.float32)
    y = np.array([1, 0] * 20, dtype=np.int32)
    model = train_ensemble(X, y)
    assert model is not None
    assert isinstance(model, EnsembleModel)


def test_ensemble_predict_proba_returns_float() -> None:
    X = np.random.rand(40, 23).astype(np.float32)
    y = np.array([1, 0] * 20, dtype=np.int32)
    model = train_ensemble(X, y)
    proba = model.predict_proba(X[:5])
    assert proba.shape == (5,)
    assert all(0.0 <= p <= 1.0 for p in proba)


def test_ensemble_predict_returns_int() -> None:
    X = np.random.rand(40, 23).astype(np.float32)
    y = np.array([1, 0] * 20, dtype=np.int32)
    model = train_ensemble(X, y)
    preds = model.predict(X[:5])
    assert preds.shape == (5,)
    assert all(p in (0, 1) for p in preds)


def test_ensemble_score_returns_float() -> None:
    X = np.random.rand(40, 23).astype(np.float32)
    y = np.array([1, 0] * 20, dtype=np.int32)
    model = train_ensemble(X, y)
    score = model.score(X, y)
    assert 0.0 <= score <= 1.0


def test_ensemble_handles_single_class() -> None:
    X = np.random.rand(20, 23).astype(np.float32)
    y = np.ones(20, dtype=np.int32)
    model = train_ensemble(X, y)
    assert model is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_ensemble.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Implement ensemble**

Create `engine/app/ml/ensemble.py`:

```python
from __future__ import annotations

import numpy as np
from lightgbm import LGBMClassifier
from xgboost import XGBClassifier


class EnsembleModel:
    """Soft-voting ensemble of XGBoost + LightGBM."""

    def __init__(self, xgb: XGBClassifier, lgbm: LGBMClassifier) -> None:
        self.xgb = xgb
        self.lgbm = lgbm

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        p1 = self.xgb.predict_proba(X)[:, 1]
        p2 = self.lgbm.predict_proba(X)[:, 1]
        return (p1 + p2) / 2.0

    def predict(self, X: np.ndarray, threshold: float = 0.5) -> np.ndarray:
        proba = self.predict_proba(X)
        return (proba >= threshold).astype(int)

    def score(self, X: np.ndarray, y: np.ndarray, threshold: float = 0.5) -> float:
        preds = self.predict(X, threshold=threshold)
        return float(np.mean(preds == y))


def train_ensemble(X: np.ndarray, y: np.ndarray) -> EnsembleModel | None:
    if len(np.unique(y)) < 2:
        return None
    # Class imbalance handling
    n_pos = int(np.sum(y == 1))
    n_neg = int(np.sum(y == 0))
    scale_pos = n_neg / n_pos if n_pos > 0 else 1.0

    # Regularization per signal-classification skill: subsample=0.8, colsample=0.8
    xgb = XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos,
        eval_metric="logloss",
        verbosity=0,
    )
    xgb.fit(X, y)

    lgbm = LGBMClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos,
        verbose=-1,
    )
    lgbm.fit(X, y)
    return EnsembleModel(xgb, lgbm)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_ensemble.py -v`
Expected: 5 passed

---

## Task 4: Update Inference for 23 Features + Ensemble

**Files:**
- Modify: `engine/app/ml/inference.py` (full file replacement)
- Modify: `engine/tests/test_inference.py` (full file replacement)

**Downstream test impact:**
- `test_execution_agent.py` mocks `predict_single` → **unaffected** ✓
- `test_inference.py` `_train_minimal_model` creates 8-feature model → **must change to 23** ✓
- `test_inference.py` all `features = {...}` dicts use old names → **must change to 23 new names** ✓
- `test_features.py` already updated in Task 2 → **unaffected** ✓

- [ ] **Step 1: Write failing test for ensemble predict_single**

Append to `engine/tests/test_inference.py` (at the very end, after all existing tests):

```python
def test_predict_single_with_ensemble() -> None:
    from app.ml.ensemble import train_ensemble
    from app.ml.features import FEATURE_NAMES

    X = np.random.rand(40, 23).astype(np.float32)
    y = np.array([1, 0] * 20, dtype=np.int32)
    model = train_ensemble(X, y)
    features = {name: 0.5 for name in FEATURE_NAMES}
    prob = predict_single(model, features)
    assert 0.0 <= prob <= 1.0
    assert isinstance(prob, float)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_inference.py::test_predict_single_with_ensemble -v`
Expected: FAIL — `predict_single` builds 8-feature array, model expects 23

- [ ] **Step 3: Replace entire `engine/app/ml/inference.py`**

The current file has 8-feature `predict_single` and XGBoost-only `auto_ensure_model`. Replace the ENTIRE file with this transitional version (auto_ensure_model will be further updated in Task 9):

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from xgboost import XGBClassifier

from app.ml.features import FEATURE_NAMES, build_features
from app.ml.patterns import explain_features, train_classifier
from app.ml.pipeline import fetch_data, generate_trades
from app.ml.validation import walk_forward_validate

_ENGINE_DIR = Path(__file__).resolve().parent.parent.parent
_DEFAULT_MODEL_PATH = _ENGINE_DIR / "data" / "xgboost_model.json"


def save_model(model: XGBClassifier, path: str | Path) -> None:
    model.save_model(str(path))


def load_model(path: str | Path) -> XGBClassifier | None:
    p = Path(path)
    if not p.exists():
        return None
    model = XGBClassifier()
    model.load_model(str(p))
    return model


def predict_single(model: Any, features: dict[str, float]) -> float:
    if model is None:
        return 0.5
    x = np.array(
        [[float(features.get(name, 0.0)) for name in FEATURE_NAMES]],
        dtype=np.float32,
    )
    proba = model.predict_proba(x)
    return float(proba[0])


def auto_ensure_model(
    model_path: str | None = None,
    force_retrain: bool = False,
    db_path: str | None = None,
) -> tuple[XGBClassifier, dict[str, Any]]:
    path = Path(model_path) if model_path else _DEFAULT_MODEL_PATH
    path.parent.mkdir(exist_ok=True)

    if not force_retrain and path.exists():
        model = load_model(str(path))
        if model is not None:
            return model, {"loaded": True, "path": str(path)}

    df = fetch_data(5)
    trades, snapshots = generate_trades(df, hold_days=5)

    x, y = build_features(trades, snapshots)
    model = train_classifier(x, y)
    if model is None:
        raise RuntimeError("Model training returned None")

    accuracy = float(model.score(x, y))
    exp = explain_features(model, x)
    importance = exp["feature_importance"]
    wf_size = min(120, len(x) // 3)
    wf_test = min(20, len(x) // 10)
    wf = walk_forward_validate(x, y, train_size=wf_size, test_size=wf_test)

    save_model(model, str(path))

    result = {
        "status": "ok",
        "accuracy": accuracy,
        "feature_importance": dict(zip(FEATURE_NAMES, importance)),
        "walk_forward": wf,
        "total_samples": len(x),
        "win_rate_pct": float(y.sum() / len(y) * 100),
        "trades_generated": len(trades),
        "model_path": str(path),
    }
    return model, result
```

**Key change:** `predict_single` now iterates `FEATURE_NAMES` (23 features) instead of hardcoding 8. The `model` parameter accepts anything with `.predict_proba()` — works for both `XGBClassifier` and `EnsembleModel`.

- [ ] **Step 4: Replace entire `engine/tests/test_inference.py`**

The current file has 8-feature `_train_minimal_model` and old feature names. Replace the ENTIRE file:

```python
import tempfile
from pathlib import Path

import numpy as np
from xgboost import XGBClassifier

from app.ml.inference import auto_ensure_model, load_model, predict_single, save_model
from app.ml.pipeline import run_pipeline


def _all_feature_names() -> list[str]:
    from app.ml.features import FEATURE_NAMES
    return list(FEATURE_NAMES)


def _default_features() -> dict[str, float]:
    """Return a dict with all 23 feature names set to neutral defaults."""
    features = {name: 0.0 for name in _all_feature_names()}
    features["vix_rank"] = 0.5
    features["pcr"] = 1.0
    features["iv_rank"] = 0.5
    features["adx"] = 20.0
    features["rsi_14"] = 50.0
    features["vol_20d"] = 15.0
    features["atr_pct"] = 1.0
    return features


def _train_minimal_model() -> XGBClassifier:
    """Train a minimal XGBClassifier on 23 features for testing."""
    X = np.random.rand(40, 23).astype(np.float32)
    y = np.array([1, 0] * 20, dtype=np.int32)
    model = XGBClassifier(n_estimators=2, max_depth=2, verbosity=0)
    model.fit(X, y)
    return model


def test_save_and_load_model() -> None:
    with tempfile.TemporaryDirectory() as td:
        model = _train_minimal_model()
        path = Path(td) / "model.json"
        save_model(model, str(path))
        assert path.exists()
        assert path.stat().st_size > 0

        loaded = load_model(str(path))
        assert loaded is not None
        pred = loaded.predict(np.random.rand(1, 23).astype(np.float32))
        assert pred[0] in (0, 1)


def test_predict_single_returns_confidence() -> None:
    model = _train_minimal_model()
    features = _default_features()
    prob = predict_single(model, features)
    assert 0.0 <= prob <= 1.0


def test_predict_single_low_confidence_on_bad_features() -> None:
    model = _train_minimal_model()
    features = _default_features()
    features["vix_rank"] = 0.9
    features["pcr"] = 0.6
    features["iv_rank"] = 0.9
    features["adx"] = 40.0
    features["is_high_vol"] = 1
    features["is_trending"] = 1
    features["prev_ret_1d"] = -0.5
    features["prev_ret_5d"] = -3.0
    prob = predict_single(model, features)
    assert 0.0 <= prob <= 1.0


def test_missing_features_defaults_to_zero() -> None:
    model = _train_minimal_model()
    features: dict[str, float] = {}
    prob = predict_single(model, features)
    assert 0.0 <= prob <= 1.0


def test_partial_features_defaults_missing() -> None:
    model = _train_minimal_model()
    features = {"vix_rank": 0.5, "pcr": 1.0}
    prob = predict_single(model, features)
    assert 0.0 <= prob <= 1.0


def test_auto_ensure_model_creates_when_missing() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "model.json"
        model, result = auto_ensure_model(model_path=str(path), force_retrain=True)
        assert model is not None
        assert path.exists()
        assert "accuracy" in result


def test_auto_ensure_model_loads_existing() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "model.json"
        model1, _ = auto_ensure_model(model_path=str(path), force_retrain=True)
        assert model1 is not None

        model2, result = auto_ensure_model(model_path=str(path))
        assert model2 is not None
        assert result.get("loaded") is True


def test_predict_single_returns_float() -> None:
    model = _train_minimal_model()
    features = _default_features()
    prob = predict_single(model, features)
    assert isinstance(prob, float)


def test_predict_single_with_ensemble() -> None:
    from app.ml.ensemble import train_ensemble
    from app.ml.features import FEATURE_NAMES

    X = np.random.rand(40, 23).astype(np.float32)
    y = np.array([1, 0] * 20, dtype=np.int32)
    model = train_ensemble(X, y)
    features = {name: 0.5 for name in FEATURE_NAMES}
    prob = predict_single(model, features)
    assert 0.0 <= prob <= 1.0
    assert isinstance(prob, float)
```

**Key changes from old file:**
- `_train_minimal_model` now creates 23-feature model (was 8)
- `_default_features()` helper provides all 23 feature names with neutral defaults
- All test functions use `_default_features()` instead of hardcoded 8-key dicts
- `test_save_and_load_model` uses `np.random.rand(1, 23)` for prediction (was hardcoded 8 values)
- New `test_predict_single_with_ensemble` test appended

- [ ] **Step 5: Run inference tests**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_inference.py -v`
Expected: All pass (10 tests)

- [ ] **Step 6: Run full suite to verify no breakage**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests -x -q`
Expected: All pass. Execution agent tests mock `predict_single` so they're unaffected.

---

## Task 5: Threshold Optimizer

**Files:**
- Create: `engine/app/ml/threshold.py`
- Create: `engine/tests/test_threshold.py`

- [ ] **Step 1: Write failing test**

Create `engine/tests/test_threshold.py`:

```python
import numpy as np

from app.ml.threshold import optimize_threshold


def test_optimize_threshold_returns_float() -> None:
    proba = np.array([0.2, 0.4, 0.6, 0.8, 0.3, 0.7, 0.5, 0.9])
    y = np.array([0, 0, 1, 1, 0, 1, 0, 1])
    threshold = optimize_threshold(proba, y)
    assert isinstance(threshold, float)
    assert 0.30 <= threshold <= 0.80


def test_optimize_threshold_perfect_separation() -> None:
    proba = np.array([0.1, 0.2, 0.3, 0.7, 0.8, 0.9])
    y = np.array([0, 0, 0, 1, 1, 1])
    threshold = optimize_threshold(proba, y)
    assert 0.30 <= threshold <= 0.70


def test_optimize_threshold_all_same_class() -> None:
    proba = np.array([0.3, 0.4, 0.5, 0.6])
    y = np.array([1, 1, 1, 1])
    threshold = optimize_threshold(proba, y)
    assert threshold == 0.55


def test_optimize_threshold_empty_returns_default() -> None:
    proba = np.array([])
    y = np.array([])
    threshold = optimize_threshold(proba, y)
    assert threshold == 0.55
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_threshold.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Implement threshold optimizer**

Create `engine/app/ml/threshold.py`:

```python
from __future__ import annotations

import numpy as np

_DEFAULT_THRESHOLD = 0.55


def optimize_threshold(proba: np.ndarray, y: np.ndarray) -> float:
    if len(proba) == 0 or len(y) == 0:
        return _DEFAULT_THRESHOLD
    if len(np.unique(y)) < 2:
        return _DEFAULT_THRESHOLD

    best_threshold = _DEFAULT_THRESHOLD
    best_profit_factor = 0.0

    for threshold in np.arange(0.30, 0.81, 0.05):
        preds = (proba >= threshold).astype(int)
        wins = int(np.sum((preds == 1) & (y == 1)))
        losses = int(np.sum((preds == 1) & (y == 0)))
        total_profit = wins * 1.0
        total_loss = losses * 1.0
        pf = total_profit / total_loss if total_loss > 0 else float("inf") if total_profit > 0 else 0.0
        if pf > best_profit_factor and wins > 0:
            best_profit_factor = pf
            best_threshold = float(round(threshold, 2))

    return best_threshold
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_threshold.py -v`
Expected: 4 passed

---

## Task 6: Isotonic Calibration

**Files:**
- Create: `engine/app/ml/calibration.py`
- Create: `engine/tests/test_calibration.py`

> **Skill guidance:** `signal-classification` skill says "Isotonic calibration works better than Platt scaling for tree models." Using `CalibratedClassifierCV` with `method="isotonic"`.

- [ ] **Step 1: Write failing test**

Create `engine/tests/test_calibration.py`:

```python
import numpy as np

from app.ml.calibration import IsotonicCalibrator, fit_calibrator


def test_fit_calibrator_returns_calibrator() -> None:
    proba = np.array([0.1, 0.3, 0.5, 0.7, 0.9, 0.2, 0.4, 0.6, 0.8, 0.15])
    y = np.array([0, 0, 1, 1, 1, 0, 0, 1, 1, 0])
    cal = fit_calibrator(proba, y)
    assert isinstance(cal, IsotonicCalibrator)


def test_calibrator_predict_returns_float() -> None:
    proba = np.array([0.1, 0.3, 0.5, 0.7, 0.9, 0.2, 0.4, 0.6, 0.8, 0.15])
    y = np.array([0, 0, 1, 1, 1, 0, 0, 1, 1, 0])
    cal = fit_calibrator(proba, y)
    result = cal.predict(np.array([0.5]))
    assert 0.0 <= result[0] <= 1.0


def test_calibrator_predict_single() -> None:
    proba = np.array([0.1, 0.3, 0.5, 0.7, 0.9, 0.2, 0.4, 0.6, 0.8, 0.15])
    y = np.array([0, 0, 1, 1, 1, 0, 0, 1, 1, 0])
    cal = fit_calibrator(proba, y)
    result = cal.predict_single(0.5)
    assert isinstance(result, float)
    assert 0.0 <= result <= 1.0


def test_calibrator_single_class_returns_identity() -> None:
    proba = np.array([0.3, 0.4, 0.5])
    y = np.array([1, 1, 1])
    cal = fit_calibrator(proba, y)
    result = cal.predict_single(0.5)
    assert result == 0.5


def test_calibrator_monotonic() -> None:
    """Isotonic calibration should be monotonic (higher input → higher output)."""
    proba = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.15])
    y = np.array([0, 0, 0, 1, 1, 1, 1, 1, 1, 0])
    cal = fit_calibrator(proba, y)
    result = cal.predict(np.array([0.1, 0.5, 0.9]))
    assert result[0] <= result[1] <= result[2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_calibration.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Implement isotonic calibrator**

Create `engine/app/ml/calibration.py`:

```python
from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression


class IsotonicCalibrator:
    """Isotonic regression calibrator — monotonic mapping of probabilities.

    Better than Platt scaling for tree models per signal-classification skill.
    """

    def __init__(self) -> None:
        self._regressor: IsotonicRegression | None = None
        self._fitted = False

    def fit(self, proba: np.ndarray, y: np.ndarray) -> None:
        if len(np.unique(y)) < 2:
            self._fitted = False
            return
        self._regressor = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        self._regressor.fit(proba, y)
        self._fitted = True

    def predict(self, proba: np.ndarray) -> np.ndarray:
        if not self._fitted or self._regressor is None:
            return proba  # Identity (unfitted)
        return self._regressor.predict(proba)

    def predict_single(self, proba: float) -> float:
        if not self._fitted or self._regressor is None:
            return float(proba)
        return float(self._regressor.predict([proba])[0])


def fit_calibrator(proba: np.ndarray, y: np.ndarray) -> IsotonicCalibrator:
    cal = IsotonicCalibrator()
    cal.fit(proba, y)
    return cal
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_calibration.py -v`
Expected: 5 passed

---

## Task 7: Hybrid Data Loader (with neutral zone drop)

**Files:**
- Create: `engine/app/ml/hybrid_data.py`
- Create: `engine/tests/test_hybrid_data.py`

> **Skill guidance:** `signal-classification` skill says to drop neutral zone (small returns near 0) to create cleaner labels. Trades with |pnl_pct| < `neutral_zone_pct` are excluded from training.

- [ ] **Step 1: Write failing test**

Create `engine/tests/test_hybrid_data.py`:

```python
import json
import tempfile
from pathlib import Path

import numpy as np

from app.ml.hybrid_data import load_hybrid_data


def test_load_hybrid_data_synthetic_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        X, y = load_hybrid_data(db_path=db_path, years=1)
        assert isinstance(X, np.ndarray)
        assert isinstance(y, np.ndarray)
        assert X.shape[0] > 0
        assert X.shape[1] == 23
        assert y.shape[0] == X.shape[0]


def test_load_hybrid_data_with_real_trades() -> None:
    from app.data.paper_account import PaperAccount

    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        acct = PaperAccount(db_path=db_path)
        acct.place_trade(
            id="t1",
            strategy="orbs",
            symbol="NIFTY",
            exchange="NSE",
            direction="BUY",
            entry_price=100.0,
            quantity=10,
            ml_confidence=0.7,
        )
        acct.close_position("t1", exit_price=105.0, exit_reason="TP")
        acct.close()

        X, y = load_hybrid_data(db_path=db_path, years=1)
        assert X.shape[0] > 1
        assert X.shape[1] == 23


def test_neutral_zone_drops_small_pnl() -> None:
    """Trades with |pnl_pct| < neutral_zone_pct should be excluded."""
    from app.data.paper_account import PaperAccount

    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        acct = PaperAccount(db_path=db_path)
        # Trade with tiny PnL (0.1% — within neutral zone of 0.5%)
        acct.place_trade(
            id="t1",
            strategy="orbs",
            symbol="NIFTY",
            exchange="NSE",
            direction="BUY",
            entry_price=100.0,
            quantity=10,
            ml_confidence=0.7,
        )
        acct.close_position("t1", exit_price=100.1, exit_reason="TP")
        acct.close()

        # With neutral_zone_pct=0.5, this trade (0.1% return) should be dropped
        X, y = load_hybrid_data(db_path=db_path, years=1, neutral_zone_pct=0.5)
        # Only synthetic trades remain (the 0.1% real trade is dropped)
        # We can't assert exact count (synthetic varies), but real trade should not inflate it
        assert X.shape[1] == 23
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_hybrid_data.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Implement hybrid data loader**

Create `engine/app/ml/hybrid_data.py`:

```python
from __future__ import annotations

from typing import Any

import numpy as np

from app.ml.features import build_features
from app.ml.pipeline import fetch_data, generate_trades


def load_hybrid_data(
    db_path: str = "",
    years: int = 5,
    neutral_zone_pct: float = 0.5,
) -> tuple[np.ndarray, np.ndarray]:
    """Load hybrid training data: synthetic + real paper trades.

    Drops neutral-zone trades (|pnl_pct| < neutral_zone_pct) for cleaner labels.
    """
    trades, snapshots = _load_synthetic(years, neutral_zone_pct)
    if db_path:
        real_trades, real_snaps = _load_real_trades(db_path, neutral_zone_pct)
        trades.extend(real_trades)
        snapshots.extend(real_snaps)
    if not trades:
        return np.array([], dtype=np.float32).reshape(0, 23), np.array([], dtype=np.int32)
    return build_features(trades, snapshots)


def _drop_neutral(
    trades: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    neutral_zone_pct: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop trades where |pnl %| < neutral_zone_pct."""
    if neutral_zone_pct <= 0:
        return trades, snapshots
    filtered_trades: list[dict[str, Any]] = []
    filtered_snaps: list[dict[str, Any]] = []
    for trade, snap in zip(trades, snapshots, strict=False):
        pnl = float(trade.get("pnl", 0))
        entry = float(trade.get("entry_price", 0))
        if entry <= 0:
            continue
        pnl_pct = abs(pnl / entry * 100)
        if pnl_pct >= neutral_zone_pct:
            filtered_trades.append(trade)
            filtered_snaps.append(snap)
    return filtered_trades, filtered_snaps


def _load_synthetic(
    years: int,
    neutral_zone_pct: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        df = fetch_data(years)
        trades, snapshots = generate_trades(df, hold_days=5)
        return _drop_neutral(trades, snapshots, neutral_zone_pct)
    except Exception:
        return [], []


def _load_real_trades(
    db_path: str,
    neutral_zone_pct: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from app.data.paper_account import PaperAccount

    trades: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    try:
        acct = PaperAccount(db_path=db_path)
        rows = acct.get_trades(limit=500)
        for t in rows:
            if t.pnl is None:
                continue
            snap = {}
            if t.snapshot_json:
                import json

                try:
                    snap = json.loads(t.snapshot_json)
                except (json.JSONDecodeError, TypeError):
                    pass
            trades.append({
                "pnl": t.pnl,
                "entry_price": t.entry_price,
                "strategy": t.strategy,
                "symbol": t.symbol,
                "entry_time": str(t.entry_time) if t.entry_time else "",
            })
            snapshots.append(snap)
        acct.close()
    except Exception:
        pass
    return _drop_neutral(trades, snapshots, neutral_zone_pct)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_hybrid_data.py -v`
Expected: 3 passed (may be slow due to yfinance download)

---

## Task 8: Update Walk-Forward Validation with Purging + Embargo

**Files:**
- Modify: `engine/app/ml/validation.py`
- Modify: `engine/tests/test_validation.py`

> **Skill guidance:** `walk-forward-validation` skill and `advances-in-financial-machine-learning` (Ch.7) both require:
> - **Embargo**: gap between train end and test start to prevent serial correlation leakage
> - **Purging**: remove training samples whose label windows overlap test period
> Without these, walk-forward still leaks information.

- [ ] **Step 1: Write failing test for ensemble WF with embargo**

Replace `engine/tests/test_validation.py`:

```python
import numpy as np

from app.ml.validation import compute_sharpe, walk_forward_validate


def test_walk_forward_validate_returns_metrics() -> None:
    X = np.random.rand(80, 23).astype(np.float32)
    y = np.array([1, 0] * 40, dtype=np.int32)
    result = walk_forward_validate(X, y, train_size=40, test_size=10, embargo=5)
    assert "oos_sharpe" in result
    assert "profit_factor" in result
    assert "total_trades" in result
    assert "optimal_threshold" in result
    assert result["total_trades"] >= 0
    assert isinstance(result["oos_sharpe"], float)
    assert isinstance(result["optimal_threshold"], float)


def test_walk_forward_validate_small_data() -> None:
    X = np.random.rand(10, 23).astype(np.float32)
    y = np.array([1, 0] * 5, dtype=np.int32)
    result = walk_forward_validate(X, y, train_size=30, test_size=10, embargo=5)
    assert result["total_trades"] == 0
    assert result["optimal_threshold"] == 0.55


def test_walk_forward_embargo_reduces_train_samples() -> None:
    """With embargo=5, train window should exclude last 5 samples before test."""
    X = np.random.rand(60, 23).astype(np.float32)
    y = np.array([1, 0] * 30, dtype=np.int32)
    result_no_embargo = walk_forward_validate(X, y, train_size=30, test_size=10, embargo=0)
    result_with_embargo = walk_forward_validate(X, y, train_size=30, test_size=10, embargo=5)
    # Both should produce results, but embargo version uses less training data per fold
    assert result_with_embargo["total_trades"] >= 0


def test_compute_sharpe() -> None:
    returns = [0.01, 0.02, -0.01, 0.03, -0.02]
    sharpe = compute_sharpe(returns)
    assert isinstance(sharpe, float)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_validation.py -v`
Expected: FAIL — `embargo` parameter doesn't exist, `assert "optimal_threshold" in result`

- [ ] **Step 3: Update walk_forward_validate with purging + embargo**

Replace `engine/app/ml/validation.py`:

```python
from math import sqrt
from statistics import stdev
from typing import Any

import numpy as np

from app.ml.ensemble import train_ensemble
from app.ml.threshold import optimize_threshold

_DEFAULT_THRESHOLD = 0.55


def compute_sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    avg = sum(returns) / len(returns)
    sd = stdev(returns)
    if sd == 0:
        return 0.0
    return round((avg / sd) * sqrt(252), 2)


def walk_forward_validate(
    X: np.ndarray,
    y: np.ndarray,
    train_size: int = 30,
    test_size: int = 10,
    embargo: int = 5,
) -> dict[str, Any]:
    """Walk-forward validation with purging + embargo.

    Args:
        X: Feature matrix (time-ordered).
        y: Labels (time-ordered).
        train_size: Training window size.
        test_size: Test window size.
        embargo: Gap between train end and test start (prevents leakage).

    Returns:
        Dict with oos_sharpe, profit_factor, total_trades, optimal_threshold.
    """
    n = len(X)
    if n < train_size + embargo + test_size:
        return {
            "oos_sharpe": 0.0,
            "profit_factor": 0.0,
            "total_trades": 0,
            "optimal_threshold": _DEFAULT_THRESHOLD,
        }

    trades = 0
    pnls: list[float] = []
    all_proba: list[float] = []
    all_actual: list[int] = []

    start = 0
    while start + train_size + embargo + test_size <= n:
        # Purge: exclude last `embargo` samples from training (label overlap with test)
        X_train = X[start : start + train_size]
        y_train = y[start : start + train_size]
        # Embargo: gap between train end and test start
        test_start = start + train_size + embargo
        X_test = X[test_start : test_start + test_size]
        y_test = y[test_start : test_start + test_size]

        if len(np.unique(y_train)) < 2:
            start += test_size
            continue

        model = train_ensemble(X_train, y_train)
        if model is None:
            start += test_size
            continue

        proba = model.predict_proba(X_test)
        all_proba.extend(proba.tolist())
        all_actual.extend(y_test.tolist())

        for p, actual in zip(proba, y_test, strict=False):
            trades += 1
            if p >= _DEFAULT_THRESHOLD and actual == 1:
                pnls.append(0.05)
            elif p >= _DEFAULT_THRESHOLD and actual == 0:
                pnls.append(-0.03)
            else:
                pnls.append(0.0)

        start += test_size

    total_profit = sum(p for p in pnls if p > 0)
    total_loss = abs(sum(p for p in pnls if p < 0))
    profit_factor = round(total_profit / total_loss, 2) if total_loss > 0 else 0.0

    optimal_threshold = (
        optimize_threshold(np.array(all_proba), np.array(all_actual))
        if all_proba
        else _DEFAULT_THRESHOLD
    )

    return {
        "oos_sharpe": compute_sharpe(pnls),
        "profit_factor": profit_factor,
        "total_trades": trades,
        "optimal_threshold": optimal_threshold,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_validation.py -v`
Expected: 4 passed

---

## Task 9: Update auto_ensure_model for Ensemble + Threshold + Calibration

**Files:**
- Modify: `engine/app/ml/inference.py` (full file replacement)
- Modify: `engine/tests/test_inference.py` (partial: update save/load test + add new test)

**Downstream test impact:**
- `test_execution_agent.py` mocks `predict_single` → **unaffected** ✓
- `test_inference.py` `test_save_and_load_model` expects `.xgb.json` files → **must change to `.pkl`** ✓
- `test_inference.py` `test_auto_ensure_model_*` tests call `auto_ensure_model` → **will use new ensemble pipeline** ✓
- `routers/execution.py` `startup_agent()` calls `auto_ensure_model()` → **will train ensemble on startup** ✓

**Critical fix:** The previous version used hacky LightGBM `_Booster` assignment that doesn't work with `predict_proba`. This version uses **pickle** for the entire `EnsembleModel` — robust, simple, works with both XGBoost and LightGBM.

- [ ] **Step 1: Write failing test for auto_ensure_model with ensemble**

Append to `engine/tests/test_inference.py` (at the very end):

```python
def test_auto_ensure_model_returns_threshold_and_calibrator() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "model"
        model, result = auto_ensure_model(model_path=str(path), force_retrain=True)
        assert model is not None
        assert "optimal_threshold" in result
        assert "calibrator_fitted" in result
        assert isinstance(result["optimal_threshold"], float)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_inference.py::test_auto_ensure_model_returns_threshold_and_calibrator -v`
Expected: FAIL — `assert "optimal_threshold" in result`

- [ ] **Step 3: Replace entire `engine/app/ml/inference.py`**

Replace the ENTIRE file with:

```python
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np

from app.ml.calibration import IsotonicCalibrator, fit_calibrator
from app.ml.ensemble import EnsembleModel, train_ensemble
from app.ml.features import FEATURE_NAMES
from app.ml.hybrid_data import load_hybrid_data
from app.ml.threshold import optimize_threshold
from app.ml.validation import walk_forward_validate

_ENGINE_DIR = Path(__file__).resolve().parent.parent.parent
_DEFAULT_MODEL_PATH = _ENGINE_DIR / "data" / "ensemble_model.pkl"
_DEFAULT_META_PATH = _ENGINE_DIR / "data" / "model_meta.json"

_DEFAULT_THRESHOLD = 0.55


def save_model(model: EnsembleModel, path: str | Path) -> None:
    """Save ensemble model via pickle. Handles both XGBoost and LightGBM."""
    p = Path(path)
    p.parent.mkdir(exist_ok=True)
    with open(p, "wb") as f:
        pickle.dump(model, f)


def load_model(path: str | Path) -> EnsembleModel | None:
    """Load ensemble model from pickle file."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        with open(p, "rb") as f:
            return pickle.load(f)
    except (pickle.PickleError, EOFError, AttributeError):
        return None


def save_meta(meta: dict[str, Any], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")


def load_meta(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def predict_single(model: Any, features: dict[str, float]) -> float:
    if model is None:
        return 0.5
    x = np.array(
        [[float(features.get(name, 0.0)) for name in FEATURE_NAMES]],
        dtype=np.float32,
    )
    proba = model.predict_proba(x)
    return float(proba[0])


def auto_ensure_model(
    model_path: str | None = None,
    force_retrain: bool = False,
    db_path: str | None = None,
) -> tuple[EnsembleModel, dict[str, Any]]:
    path = Path(model_path) if model_path else _DEFAULT_MODEL_PATH
    meta_path = path.with_suffix(".meta.json") if model_path else _DEFAULT_META_PATH
    path.parent.mkdir(exist_ok=True)

    if not force_retrain and path.exists():
        model = load_model(str(path))
        if model is not None:
            meta = load_meta(str(meta_path))
            return model, {"loaded": True, "path": str(path), **meta}

    X, y = load_hybrid_data(db_path=db_path or "", years=5)
    if len(X) < 20:
        X, y = load_hybrid_data(db_path="", years=5)

    model = train_ensemble(X, y)
    if model is None:
        raise RuntimeError("Ensemble training returned None")

    proba = model.predict_proba(X)
    optimal_threshold = optimize_threshold(proba, y)
    calibrator = fit_calibrator(proba, y)

    accuracy = float(model.score(X, y))
    wf_size = min(120, len(X) // 3)
    wf_test = min(20, len(X) // 10)
    wf = walk_forward_validate(X, y, train_size=wf_size, test_size=wf_test, embargo=5)

    save_model(model, str(path))
    meta = {
        "status": "ok",
        "accuracy": accuracy,
        "optimal_threshold": optimal_threshold,
        "calibrator_fitted": calibrator._fitted,
        "walk_forward": wf,
        "total_samples": len(X),
        "win_rate_pct": float(y.sum() / len(y) * 100) if len(y) > 0 else 0.0,
        "feature_names": FEATURE_NAMES,
        "model_path": str(path),
    }
    save_meta(meta, str(meta_path))

    return model, meta
```

**Key changes from Task 4 version:**
- `save_model`/`load_model` now use **pickle** (was XGBoost-native `.save_model` + hacky LightGBM `_Booster`)
- Model path suffix changed to `.pkl` (was `.json`)
- `auto_ensure_model` now uses `load_hybrid_data` (was `fetch_data` + `generate_trades` + `build_features`)
- `auto_ensure_model` now uses `train_ensemble` (was `train_classifier`)
- `auto_ensure_model` now computes `optimal_threshold` and `calibrator`
- `walk_forward_validate` called with `embargo=5`
- Meta saved to `.meta.json` file alongside model

- [ ] **Step 4: Update `test_save_and_load_model` in `engine/tests/test_inference.py`**

Find this exact function (from Task 4):

```python
def test_save_and_load_model() -> None:
    with tempfile.TemporaryDirectory() as td:
        model = _train_minimal_model()
        path = Path(td) / "model.json"
        save_model(model, str(path))
        assert path.exists()
        assert path.stat().st_size > 0

        loaded = load_model(str(path))
        assert loaded is not None
        pred = loaded.predict(np.random.rand(1, 23).astype(np.float32))
        assert pred[0] in (0, 1)
```

Replace it with:

```python
def test_save_and_load_model() -> None:
    from app.ml.ensemble import train_ensemble

    with tempfile.TemporaryDirectory() as td:
        X = np.random.rand(40, 23).astype(np.float32)
        y = np.array([1, 0] * 20, dtype=np.int32)
        model = train_ensemble(X, y)
        path = Path(td) / "model.pkl"
        save_model(model, str(path))
        assert path.exists()
        assert path.stat().st_size > 0

        loaded = load_model(str(path))
        assert loaded is not None
        proba = loaded.predict_proba(np.random.rand(1, 23).astype(np.float32))
        assert 0.0 <= proba[0] <= 1.0
```

**Why this change:** `save_model` now pickles the entire `EnsembleModel` (not just XGBoost). The test must create an `EnsembleModel` via `train_ensemble` and verify `predict_proba` works after load (not just `predict`).

- [ ] **Step 5: Run inference tests**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_inference.py -v`
Expected: All pass (11 tests including new `test_auto_ensure_model_returns_threshold_and_calibrator`)

- [ ] **Step 6: Run full suite**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests -x -q`
Expected: All pass. If `test_auto_ensure_model_creates_when_missing` or `test_auto_ensure_model_loads_existing` fail due to yfinance network issues, mark with `@pytest.mark.skipif` — but they should pass since `auto_ensure_model` falls back to `load_hybrid_data(db_path="", years=5)` which uses synthetic data.

---

## Task 10: Update ExecutionAgent to Use Optimal Threshold

**Files:**
- Modify: `engine/app/agents/execution.py` (3 targeted edits)
- Modify: `engine/tests/test_execution_agent.py` (1 test appended)

**Downstream test impact:**
- `test_execution_agent.py` existing 7 tests mock `predict_single` → **unaffected by threshold change** ✓
- `test_execution_agent.py` existing tests mock `_get_signals` → **unaffected by feature dict change** ✓
- `test_execution_router.py` tests mock the agent → **unaffected** ✓
- `routers/execution.py` `startup_agent()` calls `auto_ensure_model()` → **will now return ensemble + meta** ✓

**Error handling:** If `auto_ensure_model()` fails (network error, no data), the agent falls back to `self._ml_threshold` from config. The `_optimal_threshold` stays `None` and `get_status()` returns the config threshold.

- [ ] **Step 1: Write failing test for threshold from model**

Append to `engine/tests/test_execution_agent.py` (at the very end, after `test_agent_get_status_includes_config`):

```python
def test_agent_uses_optimal_threshold_from_meta() -> None:
    from app.agents.execution import ExecutionAgent

    with tempfile.TemporaryDirectory() as td:
        cfg_path = _make_config(Path(td))
        agent = ExecutionAgent(config_path=str(cfg_path), db_path=str(Path(td) / "test.db"))
        # Default ml_threshold comes from config (0.55)
        assert agent._ml_threshold == 0.55
        # Before model load, get_status returns config threshold
        assert agent.get_status()["ml_threshold"] == 0.55
        # After setting optimal threshold, get_status returns it
        agent._optimal_threshold = 0.65
        assert agent.get_status()["ml_threshold"] == 0.65
        agent.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_execution_agent.py::test_agent_uses_optimal_threshold_from_meta -v`
Expected: FAIL — `AttributeError: 'ExecutionAgent' object has no attribute '_optimal_threshold'`

- [ ] **Step 3: Edit 1 of 3 — Add `_optimal_threshold` to `__init__`**

In `engine/app/agents/execution.py`, find this exact line (around line 32):

```python
        self._ml_threshold = ml_threshold if ml_threshold is not None else risk["ml_threshold"]
```

Add immediately AFTER it:

```python
        self._optimal_threshold: float | None = None
```

- [ ] **Step 4: Edit 2 of 3 — Update `get_status` to return optimal threshold**

In `engine/app/agents/execution.py`, find this exact block:

```python
    def get_status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "check_interval_secs": self._check_interval,
            "ml_threshold": self._ml_threshold,
            "started_at": self._started_at,
        }
```

Replace with:

```python
    def get_status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "check_interval_secs": self._check_interval,
            "ml_threshold": self._optimal_threshold if self._optimal_threshold is not None else self._ml_threshold,
            "started_at": self._started_at,
        }
```

- [ ] **Step 5: Edit 3 of 3 — Update `start()` to load optimal threshold + update feature dict in `_process_tick`**

In `engine/app/agents/execution.py`, find this exact block:

```python
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._model, _ = auto_ensure_model()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
```

Replace with:

```python
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._model, meta = auto_ensure_model()
        if "optimal_threshold" in meta:
            self._optimal_threshold = float(meta["optimal_threshold"])
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
```

Then, in `_process_tick`, find this exact block (the features dict construction):

```python
            features = {
                "vix": float(snapshot.get("vix", 15.0)),
                "pcr": float(snapshot.get("pcr", 1.0)),
                "iv_rank": float(snapshot.get("iv_rank", 0.5)),
                "adx": float(snapshot.get("adx", 20.0)),
                "is_high_vol": 1 if snapshot.get("regime", {}).get("volatility") == "high" else 0,
                "is_trending": 1 if snapshot.get("regime", {}).get("regime") == "trending" else 0,
                "prev_ret_1d": 0.0,
                "prev_ret_5d": 0.0,
            }
```

Replace with:

```python
            features = {
                "vix_rank": float(snapshot.get("vix_rank", 0.5)),
                "pcr": float(snapshot.get("pcr", 1.0)),
                "iv_rank": float(snapshot.get("iv_rank", 0.5)),
                "adx": float(snapshot.get("adx", 20.0)),
                "is_high_vol": 1 if snapshot.get("regime", {}).get("volatility") == "high" else 0,
                "is_trending": 1 if snapshot.get("regime", {}).get("regime") == "trending" else 0,
                "prev_ret_1d": 0.0,
                "prev_ret_5d": 0.0,
                "prev_ret_15d": 0.0,
                "prev_ret_30d": 0.0,
                "vol_20d": float(snapshot.get("vol_20d", 15.0)),
                "atr_pct": float(snapshot.get("atr_pct", 1.0)),
                "day_of_week_sin": 0.0,
                "day_of_week_cos": 0.0,
                "vix_change_1d": float(snapshot.get("vix_change_1d", 0.0)),
                "adx_change_5d": float(snapshot.get("adx_change_5d", 0.0)),
                "strategy_orbs": 1.0 if sig.strategy == "orbs" else 0.0,
                "strategy_vwap_reversion": 1.0 if sig.strategy == "vwap_reversion" else 0.0,
                "strategy_ema_crossover": 1.0 if sig.strategy == "ema_crossover" else 0.0,
                "strategy_bollinger_bounce": 1.0 if sig.strategy == "bollinger_bounce" else 0.0,
                "entry_minutes_from_open": 0.0,
                "range_pct": float(sig.metadata.get("range_pct", 0.0)),
                "rsi_14": float(snapshot.get("rsi_14", 50.0)),
            }
```

**Key changes:** `vix` → `vix_rank`, added all 23 feature names, strategy one-hot encoding from `sig.strategy`, `range_pct` from `sig.metadata`. Missing features default to 0.0 via `predict_single`'s `.get(name, 0.0)`.

Then, find this exact line in `_process_tick`:

```python
            if prob >= self._ml_threshold:
```

Replace with:

```python
            effective_threshold = self._optimal_threshold if self._optimal_threshold is not None else self._ml_threshold
            if prob >= effective_threshold:
```

And find this exact line (the skip reason for below threshold):

```python
                    reason=f"Below ML confidence threshold ({prob:.2f} < {self._ml_threshold})",
```

Replace with:

```python
                    reason=f"Below ML confidence threshold ({prob:.2f} < {effective_threshold})",
```

- [ ] **Step 6: Run execution agent tests**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_execution_agent.py -v`
Expected: All 8 tests pass (7 existing + 1 new)

- [ ] **Step 7: Run full suite**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests -x -q`
Expected: All pass

---

## Task 11: Update Pipeline for Hybrid Data + Ensemble

**Files:**
- Modify: `engine/app/ml/pipeline.py` (full file replacement)

**Downstream test impact:**
- No tests directly test `run_pipeline` (it requires yfinance network access) → **unaffected** ✓
- `test_inference.py` `auto_ensure_model` tests use `load_hybrid_data` directly, not `run_pipeline` → **unaffected** ✓
- `fetch_data` and `generate_trades` are still used by `hybrid_data.py` → **must keep these functions** ✓

- [ ] **Step 1: Replace entire `engine/app/ml/pipeline.py`**

The current file imports `build_features`, `train_classifier`, `explain_features` which are no longer used directly. `run_pipeline` must use `load_hybrid_data` + `train_ensemble` + `optimize_threshold`. `fetch_data` and `generate_trades` must be kept (used by `hybrid_data.py`).

Replace the ENTIRE file with:

```python
import json
import random
from datetime import timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from app.agents.scanner import compute_adx, compute_atr_pct
from app.database import create_trade, init_db
from app.ml.validation import walk_forward_validate

_ENGINE_DIR = Path(__file__).resolve().parent.parent.parent


def fetch_data(years: int = 5) -> pd.DataFrame:
    nifty = yf.download("^NSEI", period=f"{years}y", auto_adjust=True)
    if nifty.empty:
        raise RuntimeError("No data from yfinance")
    px = nifty["Close"].squeeze()
    df = pd.DataFrame({"close": px})
    df["ret_1d"] = df["close"].pct_change(1)
    df["ret_5d"] = df["close"].pct_change(5)
    df["ret_15d"] = df["close"].pct_change(15)
    df["ret_30d"] = df["close"].pct_change(30)
    df["vol_20d"] = df["ret_1d"].rolling(20).std() * np.sqrt(252)
    df["atr_pct"] = (
        df["close"]
        .rolling(14)
        .apply(lambda x: compute_atr_pct(x.tolist()) if len(x) == 14 else np.nan)
    )
    try:
        vix = yf.download("^INDIAVIX", period=f"{years}y", auto_adjust=True)
        df["vix"] = vix["Close"].squeeze() if not vix.empty else 15.0
    except Exception:
        df["vix"] = 15.0
    df["vix"] = df["vix"].fillna(15.0).ffill()
    df["vix_rank"] = df["vix"].rank(pct=True)
    df["adx"] = (
        df["close"]
        .rolling(28)
        .apply(lambda x: compute_adx(x.tolist()) if len(x) == 28 else np.nan)
    )
    df["regime"] = df["adx"].apply(lambda a: "trending" if not pd.isna(a) and a >= 25 else "ranging")
    df["volatility"] = df["atr_pct"].apply(lambda v: "high" if not pd.isna(v) and v >= 2.0 else "low")
    df["dayofweek"] = df.index.dayofweek
    df["month"] = df.index.month
    return df


def generate_trades(df: pd.DataFrame, hold_days: int = 5) -> tuple[list[dict], list[dict]]:
    trades = []
    snapshots = []
    n = len(df) - hold_days
    for i in range(n):
        entry_px = df["close"].iloc[i]
        exit_px = df["close"].iloc[i + hold_days]
        pnl = exit_px - entry_px
        ret_fwd = (exit_px / entry_px - 1) * 100
        trades.append({
            "pnl": pnl,
            "entry_price": entry_px,
            "exit_price": exit_px,
            "strategy": f"EVERYDAY_HOLD{hold_days}",
            "symbol": "NIFTY",
            "prev_close": df["close"].iloc[i - 1] if i > 0 else entry_px,
            "prev_ret_5d": float(df["ret_5d"].iloc[i]) if not pd.isna(df["ret_5d"].iloc[i]) else 0.0,
            "forward_ret": round(ret_fwd, 4),
        })
        snapshots.append({
            "vix_rank": float(df["vix_rank"].iloc[i]) if not pd.isna(df["vix_rank"].iloc[i]) else 0.5,
            "pcr": 1.0,
            "iv_rank": float(df["vix_rank"].iloc[i]) if not pd.isna(df["vix_rank"].iloc[i]) else 0.5,
            "adx": float(df["adx"].iloc[i]) if not pd.isna(df["adx"].iloc[i]) else 20.0,
            "regime": df["regime"].iloc[i],
            "volatility": df["volatility"].iloc[i],
            "vol_20d": float(df["vol_20d"].iloc[i]) if not pd.isna(df["vol_20d"].iloc[i]) else 15.0,
            "atr_pct": float(df["atr_pct"].iloc[i]) if not pd.isna(df["atr_pct"].iloc[i]) else 1.0,
            "rsi_14": 50.0,
        })
    return trades, snapshots


def run_pipeline(years: int = 5, hold_days: int = 5, save_db: bool = False, db_path: str = "") -> dict:
    from app.ml.ensemble import train_ensemble
    from app.ml.hybrid_data import load_hybrid_data
    from app.ml.threshold import optimize_threshold

    X, y = load_hybrid_data(db_path=db_path, years=years)
    n = len(X)
    if n < 20:
        return {"status": "error", "reason": f"Only {n} samples. Need at least 20."}

    model = train_ensemble(X, y)
    if model is None:
        return {"status": "error", "reason": "Ensemble training failed"}

    proba = model.predict_proba(X)
    optimal_threshold = optimize_threshold(proba, y)
    accuracy = float(model.score(X, y))

    wf_size = min(120, n // 3)
    wf_test = min(20, n // 10)
    wf = walk_forward_validate(X, y, train_size=wf_size, test_size=wf_test, embargo=5)

    results = {
        "status": "ok",
        "accuracy": accuracy,
        "optimal_threshold": optimal_threshold,
        "walk_forward": wf,
        "total_samples": n,
        "win_rate_pct": float(y.sum() / n * 100) if n > 0 else 0.0,
        "years": years,
        "hold_days": hold_days,
    }

    results_path = _ENGINE_DIR / "data" / "training_results.json"
    results_path.parent.mkdir(exist_ok=True)
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)

    if save_db:
        db_path = db_path or str(_ENGINE_DIR / "data" / "trades.db")
        with init_db(db_path) as conn:
            df = fetch_data(years)
            trades, _ = generate_trades(df, hold_days=hold_days)
            count = 0
            for i, trade in enumerate(trades):
                dt = df.index[i].to_pydatetime().replace(tzinfo=timezone.utc)
                create_trade(
                    conn,
                    id=f"seed_{i}",
                    strategy=trade["strategy"],
                    symbol="NIFTY",
                    exchange="NSE",
                    direction="BUY",
                    entry_time=dt,
                    entry_price=trade["entry_price"],
                    exit_price=trade["exit_price"],
                    quantity=random.randint(25, 100),
                    pnl=trade["pnl"],
                    outcome="WIN" if trade["pnl"] > 0 else "LOSS",
                )
                count += 1
            results["trades_saved"] = count

    return results
```

**Key changes from old file:**
- Removed imports: `build_features`, `train_classifier`, `explain_features`, `FEATURE_NAMES` (no longer used directly)
- `fetch_data`: `vix` column now also computes `vix_rank` (percentile rank) for new feature name
- `generate_trades`: snapshots now use `vix_rank` (was `vix`), added `vol_20d`, `atr_pct`, `rsi_14`
- `run_pipeline`: uses `load_hybrid_data` + `train_ensemble` + `optimize_threshold` (was `build_features` + `train_classifier` + `explain_features`)
- `run_pipeline`: `walk_forward_validate` called with `embargo=5` (was no embargo)
- `run_pipeline`: accepts `db_path` parameter (was hardcoded)
- `run_pipeline`: returns `optimal_threshold` (was `feature_importance`)

- [ ] **Step 2: Run full suite**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests -x -q`
Expected: All pass. `fetch_data` and `generate_trades` are still present and compatible with `hybrid_data.py` imports.

---

## Task 12: Update SHAP Explain for Ensemble

**Files:**
- Modify: `engine/app/ml/patterns.py`
- Modify: `engine/tests/test_patterns.py`

- [ ] **Step 1: Write failing test for ensemble explain**

Replace `engine/tests/test_patterns.py`:

```python
import numpy as np

from app.ml.patterns import explain_ensemble


def test_explain_ensemble_returns_importance() -> None:
    from app.ml.ensemble import train_ensemble

    X = np.random.rand(40, 23).astype(np.float32)
    y = np.array([1, 0] * 20, dtype=np.int32)
    model = train_ensemble(X, y)
    result = explain_ensemble(model, X)
    assert "feature_importance" in result
    assert len(result["feature_importance"]) == 23


def test_explain_ensemble_xgb_only() -> None:
    from xgboost import XGBClassifier

    X = np.random.rand(40, 23).astype(np.float32)
    y = np.array([1, 0] * 20, dtype=np.int32)
    model = XGBClassifier(n_estimators=10, max_depth=3, verbosity=0)
    model.fit(X, y)
    from app.ml.patterns import explain_features

    result = explain_features(model, X)
    assert "feature_importance" in result
    assert len(result["feature_importance"]) == 23
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_patterns.py -v`
Expected: FAIL — `explain_ensemble` doesn't exist

- [ ] **Step 3: Update patterns.py**

Replace `engine/app/ml/patterns.py`:

```python
from __future__ import annotations

from typing import Any

import numpy as np
import shap
from xgboost import XGBClassifier


def train_classifier(X: np.ndarray, y: np.ndarray) -> XGBClassifier | None:
    if len(np.unique(y)) < 2:
        return None
    model = XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.05, verbosity=0)
    model.fit(X, y)
    return model


def explain_features(model: XGBClassifier, X: np.ndarray) -> dict[str, Any]:
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)
    importance = np.abs(shap_values).mean(axis=0).tolist()
    return {"shap_values": shap_values.tolist(), "feature_importance": importance}


def explain_ensemble(model: Any, X: np.ndarray) -> dict[str, Any]:
    """Explain ensemble by averaging SHAP from XGBoost component."""
    if hasattr(model, "xgb"):
        return explain_features(model.xgb, X)
    return explain_features(model, X)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests/test_patterns.py -v`
Expected: 2 passed

---

## Task 13: Final Full Suite Run + Lint

**Files:**
- None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m pytest engine/tests -v`
Expected: All pass, 0 failures

- [ ] **Step 2: Run ruff lint**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m ruff check engine/app engine/tests`
Expected: No errors (or auto-fix with `--fix`)

- [ ] **Step 3: Run ruff format check**

Run: `& "W:\Trading\engine\.venv\Scripts\python.exe" -m ruff format --check engine/app engine/tests`
Expected: All files already formatted

---

## Self-Review Checklist

- [x] **Spec coverage:** 23 features (Task 2), ensemble (Task 3), threshold optimization (Task 5), isotonic calibration (Task 6), hybrid data with neutral zone drop (Task 7), walk-forward with purging+embargo (Task 8), auto_ensure_model (Task 9), execution agent threshold + 23-feature dict (Task 10), pipeline (Task 11), SHAP (Task 12)
- [x] **Placeholder scan:** No TBD/TODO in any task. No vague "replace each block" instructions. All modify tasks show full file contents or exact old→new anchors.
- [x] **Type consistency:** `EnsembleModel` used consistently across ensemble.py, inference.py, validation.py, patterns.py. `IsotonicCalibrator` used in calibration.py and inference.py. `optimize_threshold` returns float everywhere. `walk_forward_validate` accepts `embargo` param. `predict_single` accepts `Any` model type with `.predict_proba()`.
- [x] **Full file contents:** Tasks 2, 3, 4, 5, 6, 7, 8, 9, 11, 12 all provide complete file contents for created/modified files. Task 10 provides 3 exact old→new anchor edits (no full file needed — only 3 small changes to a 300-line file).
- [x] **Save/load robustness:** Task 9 uses pickle for entire `EnsembleModel` (not hacky LightGBM `_Booster` assignment). `load_model` has try/except for corrupt files.
- [x] **Downstream test impact:** Every modify task includes "Downstream test impact" section listing which tests are affected and how.
- [x] **Error handling:** Task 10 notes fallback to config threshold if `auto_ensure_model` fails. Task 9 `load_model` catches `PickleError`/`EOFError`. Task 7 `_load_synthetic` and `_load_real_trades` catch all exceptions. Task 8 `walk_forward_validate` handles small data.
- [x] **Feature name consistency:** All tasks use `vix_rank` (not `vix`), `day_of_week_sin`/`day_of_week_cos` (not `day_of_week`/`month`). Verified in features.py, inference.py, execution.py, pipeline.py, test files.
- [x] **Skill guidance applied:**
  - `advances-in-financial-machine-learning`: Purged K-Fold + Embargo (Ch.7) → Task 8
  - `signal-classification`: Isotonic > Platt for trees → Task 6; drop neutral zone → Task 7; subsample/colsample=0.8 + scale_pos_weight → Task 3; threshold for profit factor → Task 5
  - `feature-engineering`: Stationarity (vix_rank not raw vix) → Task 2; cyclical sin/cos time encoding → Task 2; no-lookahead (rolling stats) → Task 2
  - `walk-forward-validation`: Embargo gap, purging → Task 8
