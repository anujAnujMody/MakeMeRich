"""The luck-adjusted score must never flatter a strategy.

This exists because of a real failure on 2026-08-01. The first full run of
the strategy library printed:

    orb60   1,271 trades   47.0% win   mean R -0.062   t -2.27   luck-adj 1.000  BEATS LUCK

A strategy that loses money on 1,271 trades, with a t-statistic of -2.27,
was awarded a perfect confidence score and the label "BEATS LUCK". The cause
was arithmetic, not judgement: `expected_max_sharpe_under_null` evaluates
`norm.ppf(1 - 1/N)`, which at N=1 is `ppf(0)` — negative infinity. The
benchmark became -inf, and "probability the true Sharpe exceeds -inf" is
1.000 exactly.

That is the single most dangerous class of bug in this project: a
confidently wrong number, on the one column intended to stop confidently
wrong numbers reaching the user. So the properties below are pinned
directly rather than left to the metric's own unit tests.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import sqlalchemy as sa

from te.backtest.strategy_lab import TRIAL_SCOPE, StrategyTrade, _score
from te.ml.trials import TrialLedger


def _trades(r_multiples: list[float]) -> list[StrategyTrade]:
    base = dt.datetime(2026, 3, 10, 5, 0, tzinfo=dt.UTC)
    return [
        StrategyTrade(
            entry_ts=base + dt.timedelta(minutes=i),
            direction="long_call",
            option_symbol="NIFTY10MAR26C24500",
            barrier="target" if r > 0 else "stop",
            r_multiple=r,
        )
        for i, r in enumerate(r_multiples)
    ]


def _losing(n: int = 200) -> list[StrategyTrade]:
    """A realistically shaped loser: wins are full-sized, losses are full
    stops, and there are more losses. Mean R lands around -0.1."""
    return _trades([1.0 if i % 5 < 2 else -1.0 for i in range(n)])


def _ledger(tmp_path: Path) -> TrialLedger:
    return TrialLedger(sa.create_engine(f"sqlite:///{tmp_path / 'trials.db'}"))


def _result(trades: list[StrategyTrade], ledger: TrialLedger | None = None, strategy: str = "loser"):  # noqa: ANN202
    return _score(
        strategy=strategy,
        instrument="NIFTY",
        trades=trades,
        unlabelled=0,
        first_day=dt.date(2024, 1, 1),
        last_day=dt.date(2026, 5, 5),
        trial_ledger=ledger,
        run_id="test",
    )


def test_a_losing_strategy_never_beats_luck_even_with_no_ledger() -> None:
    """The exact 2026-08-01 regression. With no ledger the trial count is 1,
    which is the degenerate case that produced `ppf(0)` and a perfect score.
    A money-losing strategy must score near zero regardless."""
    result = _result(_losing())
    assert result.mean_r < 0, "fixture is meant to lose money"
    assert not result.beats_luck, "a losing strategy was reported as beating luck"
    assert result.deflated < 0.5, f"a losing strategy scored {result.deflated:.3f}"


def test_a_losing_strategy_never_beats_luck_with_a_ledger(tmp_path: Path) -> None:
    result = _result(_losing(), _ledger(tmp_path))
    assert not result.beats_luck
    assert result.deflated < 0.5


def test_the_score_falls_as_more_strategies_are_tried(tmp_path: Path) -> None:
    """The whole point of deflation: the same result must be worth LESS
    confidence once you admit how many strategies were tried. Without this,
    a library of thirty is a machine for manufacturing winners."""
    winner = _trades([1.0 if i % 3 else -1.0 for i in range(200)])
    ledger = _ledger(tmp_path)

    first = _result(winner, ledger, strategy="w1")
    # Padded with trials drawn from the SAME distribution as the winner. The
    # comparison has to hold the trial distribution fixed, or it measures the
    # distribution changing rather than the trial count rising — an earlier
    # version padded with losers, which lowered the observed mean Sharpe and
    # made the score go UP. That finding is what put the zero floor on
    # `mean_sharpe` in `_score`.
    for i in range(60):
        _result(winner, ledger, strategy=f"filler{i}")
    later = _result(winner, ledger, strategy="w2")

    assert later.n_trials_at_scoring > first.n_trials_at_scoring
    assert later.deflated <= first.deflated, (
        "the same result scored no lower after 60 more strategies were tried — "
        "the multiple-testing correction is not engaging"
    )


def test_padding_the_library_with_losers_cannot_inflate_a_winner(tmp_path: Path) -> None:
    """The perverse-incentive guard, pinned separately because it is the
    subtler half of the same bug.

    Adding strategies that do BADLY must never make a good one score higher.
    Under the unmodified formula it does: the observed mean trial Sharpe
    falls, dragging the benchmark below zero. Flooring the mean at zero is
    what stops a library of deliberate junk from manufacturing confidence.
    """
    winner = _trades([1.0 if i % 3 else -1.0 for i in range(200)])
    ledger = _ledger(tmp_path)

    alone = _result(winner, ledger, strategy="w1")
    for i in range(60):
        _result(_losing(60), ledger, strategy=f"junk{i}")
    padded = _result(winner, ledger, strategy="w2")

    assert padded.deflated <= alone.deflated + 1e-9, (
        f"adding 60 losing strategies raised the winner's score from {alone.deflated:.4f} "
        f"to {padded.deflated:.4f} — junk in the library is inflating confidence"
    )


def test_every_run_is_recorded_and_the_count_only_grows(tmp_path: Path) -> None:
    """The ledger has no delete path by design — resetting trials to make a
    score look better is the prior failure mode this structurally blocks."""
    ledger = _ledger(tmp_path)
    before = ledger.n_trials(TRIAL_SCOPE)
    _result(_losing(), ledger)
    _result(_losing(), ledger)
    assert ledger.n_trials(TRIAL_SCOPE) == before + 2


def test_a_thin_sample_is_refused_rather_than_scored(tmp_path: Path) -> None:
    """Below 30 trades nothing is reported — a win rate on 12 trades is
    noise wearing a percentage sign."""
    result = _result(_trades([1.0, -1.0, 1.0] * 4), _ledger(tmp_path))
    assert result.trades < 30
    assert result.deflated == 0.0
    assert not result.beats_luck


def test_a_flat_strategy_does_not_score_above_the_bar(tmp_path: Path) -> None:
    """Exactly break-even before costs is still not an edge."""
    result = _result(_trades([1.0, -1.0] * 100), _ledger(tmp_path))
    assert not result.beats_luck


@pytest.mark.parametrize("n_trials_hint", [1, 2, 50])
def test_the_score_stays_a_probability(tmp_path: Path, n_trials_hint: int) -> None:
    """A score outside [0, 1] means the arithmetic has broken again, in a way
    a reader would have no way to notice."""
    ledger = _ledger(tmp_path)
    for i in range(n_trials_hint - 1):
        _result(_losing(60), ledger, strategy=f"pad{i}")
    result = _result(_losing(), ledger)
    assert 0.0 <= result.deflated <= 1.0
