"""The ONE deflation path every backtest lab scores through.

### Why this module exists

`strategy_lab` (single-leg) and `spread_lab` (credit spreads) both turn a
list of R-multiples into a deflated score, and until 2026-08-02 each carried
its own copy of that arithmetic. The copies were byte-identical, and
`spread_lab._score`'s docstring said so out loud — "deliberately mirrors
`strategy_lab._score`, same guards against the two failures found there".

That comment names the risk precisely. The three floors below are not
niceties; each one is a correctness fix for a measured failure where the
project confidently reported a losing strategy as real. A second copy means
the NEXT such discovery gets fixed once and silently missed in the other
lab, and the symptom is a "BEATS LUCK" verdict that only one code path
would have produced.

So the guards live here, once, and a lab supplies only its `scope` and the
fields specific to its own result type.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from te.ml.metrics import deflated_sharpe_ratio
from te.ml.trials import TrialLedger

#: Everything needed to deflate one sample, before the trial count is known.
#: Kept separate so a whole batch can be recorded BEFORE any of it is
#: scored — see `score_many`.
_Prepared = tuple[list[float], float, float, float, float, float]

#: Below this, a result is not scored at all. A handful of trades cannot
#: distinguish a rule from noise, and a Sharpe over 5 samples is meaningless
#: regardless of how good it looks.
MIN_TRADES = 30


@dataclass(frozen=True)
class ScoredSample:
    """The scored core shared by every lab's result type."""

    n: int
    win_rate: float
    mean_r: float
    sharpe: float
    t_stat: float
    deflated: float
    n_trials_at_scoring: int


def score_many(
    samples: dict[str, list[float]],
    *,
    scope: str,
    config_hash_for: dict[str, str],
    trial_ledger: TrialLedger | None,
    run_id: str,
) -> dict[str, ScoredSample | None]:
    """Scores a whole batch against ONE trial count.

    Scoring a library one strategy at a time is subtly unfair: each call
    records its own trial before reading the count, so the first strategy in
    the list is judged against N+1 trials and the last against N+32. Two
    identical strategies then get different deflated scores purely from
    their position in the list — measured at up to 0.015 of score near the
    region that matters. Never enough to cross the 0.95 bar, but a number
    shown to a user must not depend on iteration order at all.

    So every trial in the batch is recorded FIRST, then the count and trial
    history are read ONCE, and every member is deflated against the same
    benchmark.
    """
    prepared: dict[str, _Prepared | None] = {}
    for name, rs in samples.items():
        prepared[name] = _prepare(rs)

    # PHASE 1 — record every trial in the batch before reading any count.
    if trial_ledger is not None:
        for name, ready in prepared.items():
            if ready is None:
                continue
            trial_ledger.record(kind=scope, config_hash=config_hash_for[name], sharpe=ready[3], run_id=run_id)

    # PHASE 2 — one count, one trial history, shared by everyone.
    n_trials, mean_sharpe, observed_var = _trial_population(trial_ledger, scope)
    return {
        name: None if ready is None else _deflate(ready, n_trials, mean_sharpe, observed_var)
        for name, ready in prepared.items()
    }


def score_r_multiples(
    rs: list[float],
    *,
    scope: str,
    config_hash: str,
    trial_ledger: TrialLedger | None,
    run_id: str,
) -> ScoredSample | None:
    """Scores `rs` and records the trial. `None` when there is too little
    evidence to score at all (fewer than `MIN_TRADES`), which the caller
    turns into its own empty result.

    `trial_ledger` is optional only so a caller can measure something without
    polluting the count (a smoke test, a re-check of an already-recorded
    run). Any result shown to a user MUST pass one, or its `deflated` score
    is computed against an undercounted N and is therefore too generous.

    `scope` decides which population the multiple-testing correction counts
    against — single-leg and spread backtests are deliberately separate
    scopes, since they are not searches over the same space.
    """
    ready = _prepare(rs)
    if ready is None:
        return None

    # RECORDED BEFORE SCORING, deliberately. The deflation must include this
    # very trial — a strategy scored against a count that excludes itself is
    # judged against a smaller search than actually happened.
    if trial_ledger is not None:
        trial_ledger.record(kind=scope, config_hash=config_hash, sharpe=ready[3], run_id=run_id)
    n_trials, mean_sharpe, observed_var = _trial_population(trial_ledger, scope)
    return _deflate(ready, n_trials, mean_sharpe, observed_var)


def _prepare(rs: list[float]) -> _Prepared | None:
    """The per-sample statistics, computed before any trial count is known.
    `None` when there is too little evidence to score at all."""
    n = len(rs)
    if n < MIN_TRADES:
        return None
    mean_r = statistics.fmean(rs)
    stdev = statistics.stdev(rs)
    sharpe = mean_r / stdev if stdev > 0 else 0.0
    return rs, float(n), mean_r, sharpe, sharpe * math.sqrt(n), sum(1 for r in rs if r > 0) / n


def _trial_population(trial_ledger: TrialLedger | None, scope: str) -> tuple[int, float, float]:
    """`(n_trials, mean_sharpe, observed_var)` across every trial ever run
    under `scope`. Read ONCE per batch so everyone is judged alike."""
    if trial_ledger is None:
        return 1, 0.0, 0.0
    n_trials = max(1, trial_ledger.n_trials(scope))
    history = trial_ledger.trial_sharpes(scope)
    if len(history) < 2:
        return n_trials, 0.0, 0.0
    return n_trials, statistics.fmean(history), statistics.pvariance(history)


def _deflate(ready: _Prepared, n_trials: int, mean_sharpe: float, observed_var: float) -> ScoredSample:
    rs, n_float, mean_r, sharpe, t_stat, win_rate = ready
    n = int(n_float)
    stdev = statistics.stdev(rs)

    # The variance of Sharpe ratios ACROSS trials, floored at the sampling
    # variance a single Sharpe estimate carries anyway (~1/n_obs).
    #
    # Two degenerate cases this removes, both measured:
    #
    # * A library of near-identical results drives the observed variance to
    #   zero, which collapses the expected-maximum term and lets almost
    #   anything clear the bar. Padding with 60 similar losers took a
    #   winner's score from 0.016 to 0.933.
    # * With one trial there is no observed variance at all, and the previous
    #   fallback of 1.0 was an arbitrary and far harsher assumption — so the
    #   one-trial and many-trial cases were not on the same scale and
    #   "more trials lowers the score" did not hold.
    #
    # Flooring at 1/n_obs is the principled version: even if every trial had
    # an identical TRUE Sharpe, the estimates would still scatter by at least
    # their own sampling error.
    var_sharpe = max(observed_var, 1.0 / n)

    deflated = deflated_sharpe_ratio(
        sr_hat=sharpe,
        n_obs=n,
        skewness=_skew(rs, mean_r, stdev),
        kurtosis=_kurtosis(rs, mean_r, stdev),
        # FLOORED AT ZERO, a deliberate deviation from the published formula.
        #
        # The benchmark is the expected maximum Sharpe across trials, built
        # from the observed MEAN and variance of those trials. Our library is
        # mostly losers, so the observed mean is negative — which drags the
        # benchmark below zero and makes a mildly positive strategy look
        # excellent. Measured: the same winning result scored 0.016 with one
        # trial recorded and 0.9999 after 60 LOSING strategies were added.
        #
        # That is a perverse incentive — padding the library with junk would
        # inflate every real result — and it points the correction the wrong
        # way. The bar may never sit below "no skill at all", so the mean is
        # floored at zero. The variance term still does its job of widening
        # the bar as the search broadens.
        mean_sharpe=max(0.0, mean_sharpe),
        var_sharpe=var_sharpe,
        # FLOORED AT TWO, and this is a correctness fix, not a nicety.
        # `expected_max_sharpe_under_null` evaluates `norm.ppf(1 - 1/N)`,
        # which at N=1 is `ppf(0)` = negative infinity. The benchmark then
        # becomes -inf and the deflated score comes back as a clean 1.000 —
        # observed on 2026-08-01 reporting "BEATS LUCK" for a strategy with
        # a mean R of -0.062 and a t-statistic of -2.27.
        #
        # That is the exact failure this column exists to prevent, so the
        # degenerate case is removed rather than documented: with two
        # trials the benchmark is a finite ~0.5 Sharpe, and a losing
        # strategy scores near zero as it must.
        n_trials=max(2, n_trials),
    )

    return ScoredSample(
        n=n,
        win_rate=win_rate,
        mean_r=mean_r,
        sharpe=sharpe,
        t_stat=t_stat,
        deflated=deflated,
        n_trials_at_scoring=n_trials,
    )


def _skew(values: list[float], mean: float, stdev: float) -> float:
    if stdev == 0:
        return 0.0
    return sum(((v - mean) / stdev) ** 3 for v in values) / len(values)


def _kurtosis(values: list[float], mean: float, stdev: float) -> float:
    """NON-excess kurtosis (3.0 for a normal), which is what
    `probabilistic_sharpe_ratio` expects."""
    if stdev == 0:
        return 3.0
    return sum(((v - mean) / stdev) ** 4 for v in values) / len(values)
