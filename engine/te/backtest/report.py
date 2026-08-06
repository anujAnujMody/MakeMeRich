"""`build_report()` — net metrics (Sharpe, max drawdown, win rate, profit
factor) computed from `te.backtest.engine.ClosedTrade.net_pnl` ONLY, never
`gross_pnl` — the plan's net-ness discipline extends into the backtest
report exactly as it does into `te.domain.pnl`. Also
`stationary_bootstrap_drawdown_envelope()` — a Monte Carlo drawdown envelope
built by resampling the trade P&L sequence with replacement in geometric-
length blocks (Politis & Romano's stationary bootstrap), which feeds Phase
7's live-decay monitor's Tier-2 "drawdown vs the backtest's own bootstrapped
95th-percentile envelope" hard-halt check. Built here since it's a
backtest-report artifact, not a live-monitoring one.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from te.backtest.engine import ClosedTrade
from te.domain.money import Paise


@dataclass(frozen=True)
class BacktestReport:
    n_trades: int
    win_rate: float
    profit_factor: float | None  # None when there are no losing trades to divide by
    # Per-TRADE Sharpe (mean/std, un-annualized — see `sharpe()`).
    # None when n_trades < 2 (no variance to compute against).
    sharpe: float | None
    max_drawdown_paise: Paise  # always <= 0
    net_pnl_paise: Paise


def _equity_curve(net_pnls: list[int]) -> list[int]:
    curve: list[int] = []
    running = 0
    for pnl in net_pnls:
        running += pnl
        curve.append(running)
    return curve


def max_drawdown(net_pnls: list[int]) -> int:
    """Largest peak-to-trough decline of the cumulative net-P&L equity
    curve built from `net_pnls`, in paise. Always `<= 0` (0 when the curve
    never dips below a prior peak, including the empty-sequence case)."""
    curve = _equity_curve(net_pnls)
    peak = 0
    worst = 0
    for value in curve:
        peak = max(peak, value)
        worst = min(worst, value - peak)
    return worst


def sharpe(net_pnls: list[int]) -> float | None:
    """PER-OBSERVATION (per-trade) sample Sharpe of a net-P&L sequence:
    `mean / std(ddof=1)`, with NO sample-size or annualization scaling.
    `None` when there is nothing to measure against: fewer than 2
    observations, or zero dispersion.

    **Deliberately un-annualized.** This is the convention
    `te.ml.metrics.probabilistic_sharpe_ratio()` requires of its `sr_hat`
    ("never annualized inside this formula") and the one
    `te.ml.train.train_meta_model` computes its own `sr_hat` with
    (`returns.mean() / returns.std(ddof=1)`), so the backtest report, the
    live rolling monitor and the DSR pipeline can never quietly disagree on
    the formula.

    An earlier version multiplied by `sqrt(n)`, which is a t-statistic, not
    a Sharpe ratio: it grows without bound as more trades accumulate even
    when the return distribution is unchanged. If a human-facing ANNUALIZED
    figure is ever wanted, scale this by `sqrt(trades_per_year)` derived
    from the real `entry_ts`/`exit_ts` time span at the display site — never
    by the trade COUNT. Nothing displays one today, so no such helper
    exists here yet.

    Shared with `te.risk.monitors.RollingPerformance` (Tier 3)."""
    n = len(net_pnls)
    if n < 2:
        return None
    mean = sum(net_pnls) / n
    variance = sum((p - mean) ** 2 for p in net_pnls) / (n - 1)
    std = math.sqrt(variance)
    if std == 0:
        return None
    return mean / std


def profit_factor(net_pnls: list[int]) -> float | None:
    """Gross wins / gross losses. `None` when there are no losing trades to
    divide by. Shared with `te.risk.monitors.RollingPerformance`."""
    gross_win = sum(p for p in net_pnls if p > 0)
    gross_loss = -sum(p for p in net_pnls if p < 0)
    if gross_loss <= 0:
        return None
    return gross_win / gross_loss


def build_report(trades: list[ClosedTrade]) -> BacktestReport:
    """Every figure here is derived from `ClosedTrade.net_pnl` — gross P&L
    never appears in this report, per the plan's net-ness discipline."""
    net_pnls = [int(t.net_pnl) for t in trades]
    n = len(net_pnls)
    if n == 0:
        return BacktestReport(
            n_trades=0, win_rate=0.0, profit_factor=None, sharpe=None,
            max_drawdown_paise=Paise(0), net_pnl_paise=Paise(0),
        )

    win_rate = len([p for p in net_pnls if p > 0]) / n

    return BacktestReport(
        n_trades=n,
        win_rate=win_rate,
        profit_factor=profit_factor(net_pnls),
        sharpe=sharpe(net_pnls),
        max_drawdown_paise=Paise(max_drawdown(net_pnls)),
        net_pnl_paise=Paise(sum(net_pnls)),
    )


def stationary_bootstrap_drawdown_envelope(
    net_pnls: list[int],
    *,
    n_simulations: int = 1000,
    expected_block_length: float = 5.0,
    percentiles: tuple[float, ...] = (0.5, 0.95, 0.99),
    seed: int | None = None,
) -> dict[float, int]:
    """Resamples `net_pnls` with replacement `n_simulations` times using
    Politis & Romano's stationary bootstrap (geometric block lengths, mean
    `expected_block_length`), computing `max_drawdown()` of each resampled
    path, and returns the requested percentiles of the resulting drawdown
    distribution (each value `<= 0`; a HIGHER percentile means a WORSE —
    more negative — drawdown, e.g. `result[0.95]` is the 95th-percentile
    worst-case drawdown outcome)."""
    if not percentiles:
        raise ValueError("percentiles must be non-empty")
    if not all(0 <= p <= 1 for p in percentiles):
        raise ValueError(f"every percentile must be in [0, 1], got {percentiles!r}")
    if not net_pnls:
        return dict.fromkeys(percentiles, 0)

    rng = random.Random(seed)
    n = len(net_pnls)
    restart_probability = 1.0 / expected_block_length

    drawdown_magnitudes: list[int] = []  # non-negative; sorted ascending below
    for _ in range(n_simulations):
        path: list[int] = []
        idx = rng.randrange(n)
        while len(path) < n:
            path.append(net_pnls[idx])
            idx = rng.randrange(n) if rng.random() < restart_probability else (idx + 1) % n
        drawdown_magnitudes.append(-max_drawdown(path))

    drawdown_magnitudes.sort()
    result: dict[float, int] = {}
    for p in percentiles:
        rank = min(n_simulations - 1, max(0, round(p * (n_simulations - 1))))
        result[p] = -drawdown_magnitudes[rank]
    return result
