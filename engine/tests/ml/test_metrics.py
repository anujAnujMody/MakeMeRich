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
    _performance,
    deflated_sharpe_ratio,
    expected_max_sharpe_under_null,
    expected_max_z,
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


def test_psr_accepts_the_minimum_two_observations_but_rejects_one() -> None:
    """`n_obs=2` is the smallest T for which `sqrt(T-1)` is meaningful and
    must be accepted; `n_obs=1` must be rejected. A `<` -> `<=` flip on the
    guard would reject the valid boundary (`n_obs=2`) with the same error."""
    result = probabilistic_sharpe_ratio(sr_hat=0.1, sr_benchmark=0.0, n_obs=2, skewness=0.0, kurtosis=3.0)
    assert 0.0 <= result <= 1.0

    with pytest.raises(ValueError, match="n_obs must be >= 2"):
        probabilistic_sharpe_ratio(sr_hat=0.1, sr_benchmark=0.0, n_obs=1, skewness=0.0, kurtosis=3.0)


def test_psr_denominator_exactly_zero_raises_a_clean_error() -> None:
    """`sr_hat=1, skewness=1, kurtosis=1` drives the PSR denominator
    (`1 - skew*sr_hat + ((kurt-1)/4)*sr_hat**2`) to exactly 0. The guard is
    `<= 0`, so this exact boundary must raise the documented `ValueError` —
    a `<=` -> `<` flip would let it through and crash later on a ZeroDivisionError
    instead, an unhelpful and undocumented failure mode."""
    with pytest.raises(ValueError, match="PSR denominator is non-positive"):
        probabilistic_sharpe_ratio(sr_hat=1.0, sr_benchmark=0.0, n_obs=10, skewness=1.0, kurtosis=1.0)


def test_expected_max_sharpe_accepts_zero_variance() -> None:
    """`var_sharpe=0` is a valid (if degenerate) input — every trial ever
    run had identical Sharpe — and must not raise; only a NEGATIVE variance
    is nonsensical. A `<` -> `<=` flip on the guard would reject the valid
    boundary. With var_sharpe=0 the sqrt term vanishes, so SR0 == mean_sharpe
    exactly."""
    sr0 = expected_max_sharpe_under_null(mean_sharpe=0.05, var_sharpe=0.0, n_trials=10)
    assert sr0 == pytest.approx(0.05, abs=1e-12)

    with pytest.raises(ValueError, match="var_sharpe must be non-negative"):
        expected_max_sharpe_under_null(mean_sharpe=0.05, var_sharpe=-0.01, n_trials=10)


def test_expected_max_z_below_two_trials_is_the_flat_1_96_threshold() -> None:
    """Per the docstring, below two trials there is no multiple-testing
    problem, so `n_trials=1` (and `0`) must return exactly 1.96, NOT the
    Blom's-approximation formula (which would evaluate to `norm.ppf(0.5) ==
    0.0` at n_trials=1 — a completely different, wrong number). A `<=` ->
    `<` flip on the guard would let `n_trials=1` fall through to the formula."""
    assert expected_max_z(1) == 1.96
    assert expected_max_z(0) == 1.96
    # And n_trials=2 (first trial count where the multiple-testing
    # correction actually applies) must NOT take the flat-threshold path.
    assert expected_max_z(2) != 1.96


def test_expected_max_z_blom_approximation_matches_independently_derived_value() -> None:
    """Golden value for `n_trials=10`, re-derived independently here
    (`scipy.stats.norm.ppf` spelled out inline, never calling back into
    `te.ml.metrics`) — pins the exact `(n - 0.375) / (n + 0.25)` formula, so
    swapping `-`/`+`/`/` for a neighbouring operator changes the result."""
    n = 10
    expected = norm.ppf((n - 0.375) / (n + 0.25))
    assert expected_max_z(n) == pytest.approx(expected, abs=1e-12)
    assert expected_max_z(n) == pytest.approx(1.5466, abs=1e-4)


def test_performance_zero_variance_trial_falls_back_to_its_mean() -> None:
    """A constant-return trial (sample std == 0 via `ddof=1`) must report
    its MEAN as performance, per the docstring — not `mean / 0` (inf/nan
    from a `>` -> `>=` flip on the `std > 0` guard, since std is never
    negative so `>=0` is always true) and not `mean * std` (0, from a
    `Div` -> `Mult` flip). A second, non-degenerate trial in the same call
    pins the division itself: mean=2.0, std=2.0 -> perf=1.0, which a
    `Div` -> `Mult` flip would instead report as 4.0."""
    returns = np.array(
        [
            [1.0, 1.0, 1.0],  # constant -> std == 0 -> perf must be the mean, 1.0
            [0.0, 2.0, 4.0],  # mean=2.0, sample std (ddof=1)=2.0 -> perf = mean/std = 1.0
        ]
    )
    perf = _performance(returns)
    assert np.all(np.isfinite(perf))
    assert perf == pytest.approx([1.0, 1.0], abs=1e-9)


def test_pbo_accepts_the_minimum_s_groups_of_two() -> None:
    """`s_groups=2` is the smallest positive even group count and must be
    accepted (giving C(2,1)=2 combinations); only `s_groups < 2` (i.e. 0 or
    negative) is invalid. A `<` -> `<=` flip on the guard would reject this
    valid boundary with the same error as `s_groups=0`."""
    rng = np.random.default_rng(1)
    returns = rng.normal(loc=0.0, scale=1.0, size=(6, 40))

    result = probability_of_backtest_overfitting(returns, s_groups=2)

    assert result.n_combinations == 2  # C(2, 1)

    with pytest.raises(ValueError, match="s_groups must be a positive even number"):
        probability_of_backtest_overfitting(returns, s_groups=0)


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
