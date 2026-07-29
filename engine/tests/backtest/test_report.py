"""`te.backtest.report` — net metrics (never gross) and the stationary-
bootstrap Monte Carlo drawdown envelope."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from te.backtest.engine import ClosedTrade
from te.backtest.report import build_report, max_drawdown, sharpe, stationary_bootstrap_drawdown_envelope
from te.domain.costs import CostBreakdown
from te.domain.money import Paise
from te.domain.pnl import GrossPnl, NetPnl

TS = dt.datetime(2026, 7, 29, 9, 30, tzinfo=dt.UTC)

_ZERO_COSTS = CostBreakdown(
    brokerage=Paise(0), stt=Paise(0), exchange_txn=Paise(0), sebi=Paise(0), gst=Paise(0), stamp=Paise(0)
)


def _trade(*, net_pnl: int, gross_pnl: int = 0) -> ClosedTrade:
    return ClosedTrade(
        symbol="NIFTY", exchange="NFO", strategy="orb", direction="long_call", lots=1, lot_size=65,
        entry_premium=Paise(10_000), exit_premium=Paise(10_500), entry_ts=TS, exit_ts=TS,
        exit_reason="target", gross_pnl=GrossPnl(Paise(gross_pnl)), costs=_ZERO_COSTS, net_pnl=NetPnl(Paise(net_pnl)),
    )


def test_build_report_on_no_trades_is_the_honest_zero_state() -> None:
    report = build_report([])
    assert report.n_trades == 0
    assert report.win_rate == 0.0
    assert report.profit_factor is None
    assert report.sharpe is None
    assert report.max_drawdown_paise == 0
    assert report.net_pnl_paise == 0


def test_build_report_uses_net_pnl_never_gross() -> None:
    # gross_pnl deliberately set very different from net_pnl — the report
    # must never touch it.
    trades = [_trade(net_pnl=1_000, gross_pnl=99_999), _trade(net_pnl=-500, gross_pnl=-1)]
    report = build_report(trades)
    assert report.net_pnl_paise == 500
    assert report.win_rate == pytest.approx(0.5)


def test_build_report_win_rate_and_profit_factor() -> None:
    trades = [_trade(net_pnl=1_000), _trade(net_pnl=2_000), _trade(net_pnl=-500)]
    report = build_report(trades)
    assert report.n_trades == 3
    assert report.win_rate == pytest.approx(2 / 3)
    assert report.profit_factor == pytest.approx(3_000 / 500)


def test_build_report_profit_factor_none_with_no_losses() -> None:
    trades = [_trade(net_pnl=1_000), _trade(net_pnl=2_000)]
    report = build_report(trades)
    assert report.profit_factor is None


def test_build_report_sharpe_is_none_with_fewer_than_two_trades() -> None:
    report = build_report([_trade(net_pnl=1_000)])
    assert report.sharpe is None


def test_build_report_sharpe_positive_for_a_winning_series() -> None:
    trades = [_trade(net_pnl=1_000), _trade(net_pnl=1_100), _trade(net_pnl=900), _trade(net_pnl=1_050)]
    report = build_report(trades)
    assert report.sharpe is not None
    assert report.sharpe > 0


def test_sharpe_is_per_observation_mean_over_std_with_no_sample_size_scaling() -> None:
    """Golden value, re-derived by hand: for [100, 200, 300] the sample mean
    is 200 and the ddof=1 std is sqrt(((-100)^2 + 0^2 + 100^2) / 2) = 100, so
    the per-observation Sharpe is exactly 2.0. The old implementation
    returned 2.0 * sqrt(3) = 3.4641 — a t-statistic, not a Sharpe ratio."""
    assert sharpe([100, 200, 300]) == pytest.approx(2.0)


def test_sharpe_does_not_grow_when_the_same_series_is_observed_more_often() -> None:
    """A Sharpe ratio is scale-free in n: tiling an identical return series
    leaves it (asymptotically) unchanged — only the ddof=1 correction moves,
    and it shrinks as n grows. A sqrt(n)-scaled t-statistic would instead
    grow without bound, which is precisely the bug this pins: doubling the
    sample here would multiply it by sqrt(2) = 1.414."""
    pattern = [100, 200, 300]
    sharpe_60 = sharpe(pattern * 20)
    sharpe_120 = sharpe(pattern * 40)
    assert sharpe_60 is not None and sharpe_120 is not None
    assert sharpe_120 == pytest.approx(sharpe_60, rel=0.01)


def test_sharpe_matches_the_per_observation_convention_te_ml_metrics_requires() -> None:
    """`te.ml.metrics.probabilistic_sharpe_ratio` documents that `sr_hat` is
    "never annualized inside this formula", and `te.ml.train` sources its own
    `sr_hat` as `returns.mean() / returns.std(ddof=1)`. This report's
    `sharpe()` must agree with that convention exactly, so the backtest report
    and the DSR pipeline can never quietly disagree on the formula."""
    net_pnls = [1_000, -2_000, 1_500, -500, 3_000, -1_000, 800, -300]
    arr = np.array(net_pnls, dtype=float)
    expected = float(arr.mean() / arr.std(ddof=1))
    assert sharpe(net_pnls) == pytest.approx(expected)


def test_max_drawdown_on_monotonically_increasing_curve_is_zero() -> None:
    assert max_drawdown([100, 100, 100]) == 0


def test_max_drawdown_detects_peak_to_trough_decline() -> None:
    # equity curve: 100, 300 (peak), 50 -> drawdown from peak 300 to 50 = -250
    assert max_drawdown([100, 200, -250]) == -250


def test_max_drawdown_empty_is_zero() -> None:
    assert max_drawdown([]) == 0


def test_stationary_bootstrap_envelope_empty_trades_is_zero() -> None:
    envelope = stationary_bootstrap_drawdown_envelope([], percentiles=(0.5, 0.95))
    assert envelope == {0.5: 0, 0.95: 0}


def test_stationary_bootstrap_envelope_is_reproducible_with_seed() -> None:
    net_pnls = [1_000, -2_000, 1_500, -500, 3_000, -1_000, 800, -300]
    env_a = stationary_bootstrap_drawdown_envelope(net_pnls, n_simulations=200, seed=42)
    env_b = stationary_bootstrap_drawdown_envelope(net_pnls, n_simulations=200, seed=42)
    assert env_a == env_b


def test_stationary_bootstrap_envelope_worse_percentile_is_more_negative() -> None:
    net_pnls = [1_000, -2_000, 1_500, -500, 3_000, -1_000, 800, -300, -4_000, 2_500]
    envelope = stationary_bootstrap_drawdown_envelope(
        net_pnls, n_simulations=500, percentiles=(0.5, 0.95, 0.99), seed=7
    )
    assert envelope[0.99] <= envelope[0.95] <= envelope[0.5] <= 0


def test_stationary_bootstrap_envelope_rejects_out_of_range_percentile() -> None:
    with pytest.raises(ValueError, match="0, 1"):
        stationary_bootstrap_drawdown_envelope([1, 2, 3], percentiles=(1.5,))


def test_stationary_bootstrap_envelope_all_losses_bounds_the_worst_case() -> None:
    net_pnls = [-100] * 10
    envelope = stationary_bootstrap_drawdown_envelope(net_pnls, n_simulations=100, seed=1)
    # Every resampled path is all-losses too, so drawdown is exactly the sum.
    assert envelope[0.5] == -1_000
    assert envelope[0.95] == -1_000
    assert envelope[0.99] == -1_000
