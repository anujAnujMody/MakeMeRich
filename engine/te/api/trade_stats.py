"""Pure aggregation helpers over closed `TradeRow`s, shared by the
`dashboard`/`pnl` routers so the win-rate/avg-win/avg-loss/max-drawdown math
exists in exactly one place. `te.api` sits above `te.persistence` in the
layer rule (api imports persistence + domain; nothing imports api), so this
module may take `TradeRow`s as input but must not be imported by anything
below `te.api`.

Every field here is computed from real rows only — an empty `rows` list
produces `0`s, never a fabricated 50% win rate or similar."""

from __future__ import annotations

import math
from dataclasses import dataclass

from te.domain.money import Paise, rupees
from te.persistence.models import TradeRow

#: Below this many closed trades, a Sharpe ratio is a statistic without a
#: sample — the plan's "Banned everywhere" rule. Omit, never fabricate.
MIN_TRADES_FOR_SHARPE = 100


def sample_sharpe(net_pnls: list[int]) -> float | None:
    """PER-OBSERVATION sample Sharpe (`mean / std(ddof=1)`, no sqrt(n)
    scaling) — the same convention `te.backtest.report.sharpe()` and
    `te.ml.train`'s `sr_hat` use, deliberately duplicated rather than
    imported: `te.api` may only import `engine`/`persistence`/`domain` per
    the plan's layer rule, and `te.backtest` is a peer of `te.engine`, not
    something the API layer reaches into. Keep this in sync with
    `te.backtest.report.sharpe()` if that formula ever changes."""
    n = len(net_pnls)
    if n < 2:
        return None
    mean = sum(net_pnls) / n
    variance = sum((p - mean) ** 2 for p in net_pnls) / (n - 1)
    std = math.sqrt(variance)
    if std == 0:
        return None
    return mean / std


@dataclass(frozen=True)
class TradeSummary:
    total_pnl: float
    win_rate: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win: float
    avg_loss: float
    max_drawdown: float


def summarize_trades(rows: list[TradeRow]) -> TradeSummary:
    if not rows:
        return TradeSummary(0.0, 0.0, 0, 0, 0, 0.0, 0.0, 0.0)

    net_pnls = [row.net_pnl_paise for row in rows]
    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p < 0]

    total_pnl = sum(net_pnls)
    win_rate = (len(wins) / len(rows)) * 100.0
    avg_win = (sum(wins) / len(wins)) if wins else 0
    avg_loss = (sum(losses) / len(losses)) if losses else 0

    # Max drawdown over the equity curve implied by this trade set, in
    # closing order (oldest first) — peak-to-trough over cumulative net P&L.
    ordered = sorted(rows, key=lambda r: r.closed_at)
    cumulative = 0
    peak = 0
    max_dd = 0
    for row in ordered:
        cumulative += row.net_pnl_paise
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)

    return TradeSummary(
        total_pnl=float(rupees(Paise(total_pnl))),
        win_rate=win_rate,
        total_trades=len(rows),
        winning_trades=len(wins),
        losing_trades=len(losses),
        avg_win=float(rupees(Paise(int(avg_win)))),
        avg_loss=float(rupees(Paise(int(avg_loss)))),
        max_drawdown=float(rupees(Paise(max_dd))),
    )
