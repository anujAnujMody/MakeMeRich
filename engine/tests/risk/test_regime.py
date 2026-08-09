"""`te.risk.regime` — VIX-based SIZE THROTTLE only, never a signal generator.
Composes (multiplies) against `te.ml.gates.MLInfluence.size_multiplier`,
never replaces it."""

from __future__ import annotations

from decimal import Decimal

import pytest

from te.risk.regime import (
    RegimeThrottleConfig,
    compose_size_multipliers,
    regime_size_multiplier,
    vix_terciles,
)

HISTORY = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0]


def test_vix_terciles_split_history_into_thirds() -> None:
    lo, hi = vix_terciles(HISTORY)
    assert lo < hi
    # The literal 33rd/66th percentile of HISTORY via
    # `statistics.quantiles(HISTORY, n=3, method="inclusive")` — every other
    # test in this file only probes RELATIVE to whatever `vix_terciles()`
    # itself returns, so `lo, hi = min(history), max(history)` (full-range,
    # not terciles) would still satisfy every assertion except this one.
    assert lo == pytest.approx(13.666666666666666)
    assert hi == pytest.approx(17.333333333333332)


def test_vix_terciles_empty_history_raises() -> None:
    with pytest.raises(ValueError):
        vix_terciles([])


def test_regime_multiplier_full_size_in_low_tercile() -> None:
    lo, _hi = vix_terciles(HISTORY)
    multiplier = regime_size_multiplier(lo - 1, HISTORY)
    assert multiplier == RegimeThrottleConfig().low_tercile_multiplier
    assert multiplier == Decimal("1.0")


def test_regime_multiplier_reduced_in_mid_tercile() -> None:
    lo, hi = vix_terciles(HISTORY)
    midpoint = (lo + hi) / 2
    multiplier = regime_size_multiplier(midpoint, HISTORY)
    assert multiplier == RegimeThrottleConfig().mid_tercile_multiplier
    assert multiplier < Decimal("1.0")


def test_regime_multiplier_most_reduced_in_high_tercile() -> None:
    _lo, hi = vix_terciles(HISTORY)
    multiplier = regime_size_multiplier(hi + 5, HISTORY)
    cfg = RegimeThrottleConfig()
    assert multiplier == cfg.high_tercile_multiplier
    assert multiplier < cfg.mid_tercile_multiplier < cfg.low_tercile_multiplier


def test_regime_throttle_composes_with_ml_multiplier_not_replaces() -> None:
    regime_multiplier = Decimal("0.5")
    ml_multiplier = Decimal("0.8")

    combined = compose_size_multipliers(regime_multiplier, ml_multiplier)

    assert combined == regime_multiplier * ml_multiplier
    assert combined != regime_multiplier
    assert combined != ml_multiplier
    assert combined == Decimal("0.40")


def test_custom_config_is_respected() -> None:
    cfg = RegimeThrottleConfig(
        low_tercile_multiplier=Decimal("1.0"),
        mid_tercile_multiplier=Decimal("0.6"),
        high_tercile_multiplier=Decimal("0.3"),
    )
    lo, hi = vix_terciles(HISTORY)
    assert regime_size_multiplier(hi + 5, HISTORY, config=cfg) == Decimal("0.3")
