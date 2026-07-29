"""Probabilistic Sharpe Ratio, Deflated Sharpe Ratio, and PBO via CSCV —
the anti-fake-metrics machinery. Formulas are transcribed EXACTLY from
Bailey & Lopez de Prado's "The Deflated Sharpe Ratio" (and the companion
CSCV/PBO paper); see the plan's research notes and
`tests/ml/test_metrics.py` for an independently re-derived golden-value
check.

Deliberately pure — no I/O, no `TrialLedger` import here. `n_trials` /
`mean_sharpe` / `var_sharpe` are supplied by the caller (typically read from
`te.ml.trials.TrialLedger` elsewhere), keeping this module trivially
unit-testable and decoupled from persistence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations

import numpy as np
from scipy.stats import norm

FloatArray = np.ndarray[tuple[int, ...], np.dtype[np.float64]]

#: Euler-Mascheroni constant, as used in the expected-max-Sharpe-under-the-
#: null approximation (Bailey & Lopez de Prado, eq. for E[max{SR}]).
EULER_MASCHERONI = 0.5772156649


def probabilistic_sharpe_ratio(
    *,
    sr_hat: float,
    sr_benchmark: float,
    n_obs: int,
    skewness: float,
    kurtosis: float,
) -> float:
    """PSR(SR*) = Phi[ (SR_hat - SR*) * sqrt(T-1) / sqrt(1 - g3*SR_hat +
    ((g4-1)/4)*SR_hat^2) ].

    `sr_hat` is the observed Sharpe ratio at the SAME frequency as the
    return series it was computed from (never annualized inside this
    formula — annualize before/after, not in the middle of it). `n_obs` is
    the number of return observations (T). `kurtosis` is NON-EXCESS (a
    Gaussian series has kurtosis == 3, not 0).
    """
    if n_obs < 2:
        raise ValueError(f"n_obs must be >= 2 to form sqrt(T-1), got {n_obs!r}")
    denominator = math.sqrt(1 - skewness * sr_hat + ((kurtosis - 1) / 4) * sr_hat**2)
    if denominator <= 0:
        raise ValueError(
            f"PSR denominator is non-positive ({denominator!r}) for the given sr_hat/skewness/kurtosis — "
            "the moment inputs are inconsistent with a real return distribution"
        )
    z = (sr_hat - sr_benchmark) * math.sqrt(n_obs - 1) / denominator
    return float(norm.cdf(z))


def expected_max_sharpe_under_null(*, mean_sharpe: float, var_sharpe: float, n_trials: int) -> float:
    """SR0 = mean_sharpe + sqrt(var_sharpe) * ( (1-gamma)*Phi^-1(1 - 1/N) +
    gamma*Phi^-1(1 - 1/(N*e)) ) — the expected maximum Sharpe ratio one
    would observe across `n_trials` independent trials under the null of
    zero skill.

    `mean_sharpe`/`var_sharpe` are the mean and variance of the Sharpe
    ratios ACROSS ALL TRIALS EVER RUN (not the sampling variance of one
    trial's own Sharpe estimate — the plan's research notes call this out
    explicitly as the input people get wrong).
    """
    if n_trials < 1:
        raise ValueError(f"n_trials must be >= 1, got {n_trials!r}")
    if var_sharpe < 0:
        raise ValueError(f"var_sharpe must be non-negative, got {var_sharpe!r}")
    term_a = (1 - EULER_MASCHERONI) * float(norm.ppf(1 - 1 / n_trials))
    term_b = EULER_MASCHERONI * float(norm.ppf(1 - 1 / (n_trials * math.e)))
    return mean_sharpe + math.sqrt(var_sharpe) * (term_a + term_b)


def deflated_sharpe_ratio(
    *,
    sr_hat: float,
    n_obs: int,
    skewness: float,
    kurtosis: float,
    mean_sharpe: float,
    var_sharpe: float,
    n_trials: int,
) -> float:
    """DSR = PSR with SR* replaced by `expected_max_sharpe_under_null()` —
    the Sharpe ratio required to clear the multiple-testing bar set by
    every trial ever run (honest N, from `te.ml.trials.TrialLedger.n_trials()`
    at the call site, not from this module)."""
    sr0 = expected_max_sharpe_under_null(mean_sharpe=mean_sharpe, var_sharpe=var_sharpe, n_trials=n_trials)
    return probabilistic_sharpe_ratio(
        sr_hat=sr_hat, sr_benchmark=sr0, n_obs=n_obs, skewness=skewness, kurtosis=kurtosis
    )


@dataclass(frozen=True)
class PboResult:
    """Result of one Combinatorial Symmetric Cross-Validation (CSCV) PBO
    run — `n_combinations` is always C(s_groups, s_groups // 2)."""

    pbo: float
    n_combinations: int


def _performance(returns: FloatArray) -> FloatArray:
    """Per-trial Sharpe-like performance metric (mean / sample-std across
    the time axis), used only to rank trials within one IS/OOS split — a
    zero-variance trial's performance is defined as its mean (avoids a
    NaN/inf that would otherwise poison the ranking)."""
    mean = returns.mean(axis=1)
    std = returns.std(axis=1, ddof=1) if returns.shape[1] > 1 else np.zeros(returns.shape[0])
    with np.errstate(divide="ignore", invalid="ignore"):
        perf = np.where(std > 0, mean / std, mean)
    return perf


def probability_of_backtest_overfitting(returns: FloatArray, *, s_groups: int = 8) -> PboResult:
    """PBO via CSCV (Bailey, Borwein, Lopez de Prado, Zhu). `returns` is an
    (n_trials, n_periods) matrix of per-period returns for every trial ever
    run over the same time index.

    For each of C(s_groups, s_groups // 2) combinations of `s_groups`
    contiguous time blocks split half IS / half OOS: find the trial with
    the best IS performance, compute its RELATIVE RANK among all trials'
    OOS performance (`omega_c`, in (0, 1) via a `rank / (N+1)` estimator so
    the logit is always finite), logit-transform it
    (`lambda_c = ln(omega_c / (1 - omega_c))`), and PBO is the fraction of
    combinations where `lambda_c <= 0` — the IS winner landing in the
    bottom half OOS."""
    if s_groups % 2 != 0 or s_groups < 2:
        raise ValueError(f"s_groups must be a positive even number, got {s_groups!r}")
    returns = np.asarray(returns, dtype=float)
    if returns.ndim != 2:
        raise ValueError(f"returns must be a 2D (n_trials, n_periods) array, got shape {returns.shape!r}")
    n_trials, n_periods = returns.shape
    if n_trials < 2:
        raise ValueError("probability_of_backtest_overfitting requires at least 2 trials to rank")

    blocks = np.array_split(np.arange(n_periods), s_groups)
    half = s_groups // 2

    lambdas: list[float] = []
    for is_block_idx in combinations(range(s_groups), half):
        is_idx = np.concatenate([blocks[i] for i in is_block_idx])
        oos_block_idx = [i for i in range(s_groups) if i not in is_block_idx]
        oos_idx = np.concatenate([blocks[i] for i in oos_block_idx])
        if is_idx.size == 0 or oos_idx.size == 0:
            continue

        is_perf = _performance(returns[:, is_idx])
        oos_perf = _performance(returns[:, oos_idx])

        best_trial = int(np.argmax(is_perf))
        # Rank of the IS-winner's OOS performance among all trials, 1
        # (worst) .. n_trials (best); ties broken by argsort's stable order.
        order = np.argsort(oos_perf, kind="stable")
        rank = int(np.where(order == best_trial)[0][0]) + 1

        omega_c = rank / (n_trials + 1)
        lambda_c = math.log(omega_c / (1 - omega_c))
        lambdas.append(lambda_c)

    n_combinations = len(lambdas)
    pbo = sum(1 for lam in lambdas if lam <= 0) / n_combinations
    return PboResult(pbo=pbo, n_combinations=n_combinations)
