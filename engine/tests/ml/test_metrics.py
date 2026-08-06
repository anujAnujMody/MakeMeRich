"""Golden tests for `te.ml.metrics` — the anti-fake-metrics machinery. These
tests are standing invariants per the plan's "Verification" section and must
never be deleted or loosened to force a pass.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.stats import norm

from te.ml.metrics import (
    EULER_MASCHERONI,
    deflated_sharpe_ratio,
    expected_max_sharpe_under_null,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
)


def test_dsr_matches_bailey_lopez_worked_example() -> None:
    """A small hand-derived worked example (no literal published numbers
    available in this prompt — see the plan's own caveat), computed two
    INDEPENDENT ways: once via `te.ml.metrics`, once by re-deriving the
    arithmetic directly here with `scipy.stats.norm` calls spelled out
    inline (never calling back into `te.ml.metrics`). The two must agree to
    high precision — this proves the implementation is self-consistent and
    correctly transcribed from the paper's formulas."""
    t_obs = 200
    sr_hat = 0.15
    skew = -0.3
    kurtosis = 4.5  # non-excess; Gaussian == 3
    mean_sharpe = 0.0
    var_sharpe = 0.01
    n_trials = 50

    # --- Path 1: te.ml.metrics ---
    dsr = deflated_sharpe_ratio(
        sr_hat=sr_hat,
        n_obs=t_obs,
        skewness=skew,
        kurtosis=kurtosis,
        mean_sharpe=mean_sharpe,
        var_sharpe=var_sharpe,
        n_trials=n_trials,
    )

    # --- Path 2: arithmetic re-derived inline, scipy.stats.norm only ---
    gamma = 0.5772156649
    sr0_expected = mean_sharpe + math.sqrt(var_sharpe) * (
        (1 - gamma) * norm.ppf(1 - 1 / n_trials) + gamma * norm.ppf(1 - 1 / (n_trials * math.e))
    )
    denom_expected = math.sqrt(1 - skew * sr_hat + ((kurtosis - 1) / 4) * sr_hat**2)
    z_expected = (sr_hat - sr0_expected) * math.sqrt(t_obs - 1) / denom_expected
    dsr_expected = norm.cdf(z_expected)

    assert EULER_MASCHERONI == pytest.approx(gamma, abs=1e-10)
    assert dsr == pytest.approx(dsr_expected, abs=1e-12)

    # Recorded worked-example numbers (see report for the auditable trail):
    assert sr0_expected == pytest.approx(0.2276303093419757, abs=1e-9)
    assert dsr_expected == pytest.approx(0.14427201167125642, abs=1e-9)

    # Sanity: SR0 (the multiple-testing-adjusted benchmark) must exceed the
    # plain zero benchmark whenever n_trials > 1 and var_sharpe > 0 — more
    # trials makes the bar to clear harder, not easier.
    sr0 = expected_max_sharpe_under_null(mean_sharpe=mean_sharpe, var_sharpe=var_sharpe, n_trials=n_trials)
    assert sr0 > 0

    plain_psr = probabilistic_sharpe_ratio(
        sr_hat=sr_hat, sr_benchmark=0.0, n_obs=t_obs, skewness=skew, kurtosis=kurtosis
    )
    assert plain_psr > dsr  # deflation must make the bar harder to clear, never easier


def test_dsr_decreases_as_trials_increase() -> None:
    """Same Sharpe/skew/kurtosis/T; sweep N from 1 to 100. DSR must be
    monotonically non-increasing as N grows — more trials means a higher bar
    to clear, so the same observed performance earns a lower (or equal) DSR."""
    t_obs = 200
    sr_hat = 0.15
    skew = -0.3
    kurtosis = 4.5
    mean_sharpe = 0.0
    var_sharpe = 0.01

    values = [
        deflated_sharpe_ratio(
            sr_hat=sr_hat,
            n_obs=t_obs,
            skewness=skew,
            kurtosis=kurtosis,
            mean_sharpe=mean_sharpe,
            var_sharpe=var_sharpe,
            n_trials=n,
        )
        for n in range(1, 101)
    ]

    for earlier, later in zip(values, values[1:], strict=False):
        assert later <= earlier + 1e-12, (earlier, later)

    # Not a degenerate flat line — deflation must actually bite somewhere.
    assert values[0] > values[-1]


def test_pbo_on_pure_noise_is_near_half() -> None:
    """i.i.d. zero-mean noise across many trials should be overfit-detected
    ~50% of the time by CSCV — anything systematically far from 0.5 means
    the CSCV implementation itself is wrong; do not loosen this band to
    force a pass.

    CSCV with S=8 has only C(8,4)=70 combinations built from 8 REUSED
    contiguous blocks, so any single noise realization's PBO has high
    sampling variance (empirically stdev ~0.15-0.2 at n_trials=40..200) —
    this is an inherent property of CSCV at this S, not an implementation
    bug (verified independently by averaging many seeds, which converges
    tightly to 0.5 — see below). A single seed therefore gets a wide
    sanity band; the tight [0.35, 0.65] check is on the many-seed average."""
    rng = np.random.default_rng(0)
    n_trials, n_periods = 40, 800  # 800 % 8 == 0, so blocks are equal-sized
    returns = rng.normal(loc=0.0, scale=1.0, size=(n_trials, n_periods))

    result = probability_of_backtest_overfitting(returns, s_groups=8)

    assert result.n_combinations == 70  # C(8, 4)
    assert 0.05 <= result.pbo <= 0.95, result.pbo  # not degenerate at 0 or 1


def test_pbo_average_over_seeds_is_close_to_half() -> None:
    """The tight, low-variance check: average PBO over many independent
    noise realizations must land close to 0.5. If it's systematically far
    from 0.5, the CSCV implementation is wrong — debug it, don't loosen this."""
    pbos = []
    for seed in range(30):
        rng = np.random.default_rng(seed)
        returns = rng.normal(loc=0.0, scale=1.0, size=(40, 800))
        pbos.append(probability_of_backtest_overfitting(returns, s_groups=8).pbo)
    average = sum(pbos) / len(pbos)
    assert 0.35 <= average <= 0.65, average
