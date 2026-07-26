from collections import defaultdict
from math import sqrt
from statistics import stdev
from typing import Any

from strategies.base import TradeRecord

StatsDict = dict[str, Any]


class TradeAnalyzer:
    def __init__(self, trades: list[TradeRecord]) -> None:
        self._trades = trades

    def overall(self) -> StatsDict:
        total = len(self._trades)
        if total == 0:
            return {
                "total_trades": 0, "win_rate": 0.0, "total_pnl": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0,
                "max_drawdown": 0.0, "sharpe": 0.0,
                "winning_trades": 0, "losing_trades": 0,
            }

        wins = [t.pnl for t in self._trades if t.pnl > 0]
        losses = [t.pnl for t in self._trades if t.pnl < 0]
        win_count = len(wins)
        loss_count = len(losses)
        total_pnl = sum(t.pnl for t in self._trades)

        win_rate = (win_count / total) * 100 if total > 0 else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in self._trades:
            cumulative += t.pnl
            peak = max(peak, cumulative)
            dd = cumulative - peak
            max_dd = min(max_dd, dd)

        pnls = [t.pnl for t in self._trades]
        sharpe = 0.0
        if len(pnls) > 1 and stdev(pnls) > 0:
            sharpe = (sum(pnls) / len(pnls)) / stdev(pnls) * sqrt(252)

        return {
            "total_trades": total,
            "win_rate": round(win_rate, 2),
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown": round(max_dd, 2),
            "sharpe": round(sharpe, 2),
            "winning_trades": win_count,
            "losing_trades": loss_count,
        }

    def by_strategy(self) -> dict[str, StatsDict]:
        return self._group_by(lambda t: t.strategy)

    def by_symbol(self) -> dict[str, StatsDict]:
        return self._group_by(lambda t: t.symbol)

    def by_hour(self) -> dict[int, StatsDict]:
        return self._group_by(lambda t: t.entry_time.hour)

    def by_day(self) -> dict[str, StatsDict]:
        return self._group_by(lambda t: t.entry_time.strftime("%A"))

    def _group_by(self, key_fn) -> dict:
        groups: dict = defaultdict(list)
        for t in self._trades:
            groups[key_fn(t)].append(t)
        return {k: TradeAnalyzer(v).overall() for k, v in groups.items()}

    def streaks(self) -> StatsDict:
        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0
        for t in self._trades:
            if t.pnl > 0:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            else:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)
        return {"max_consecutive_wins": max_wins, "max_consecutive_losses": max_losses}


class ParamOptimizer:
    MIN_TRADES = 3

    def __init__(self, trades: list[TradeRecord]) -> None:
        self._trades = trades

    def grid_search(self, strategy: str, param_grid: dict[str, list[float]]) -> list[StatsDict]:
        strategy_trades = [t for t in self._trades if t.strategy == strategy]
        if len(strategy_trades) < self.MIN_TRADES:
            return []

        keys = list(param_grid.keys())
        values = list(param_grid.values())

        results: list[StatsDict] = []
        for combo in self._product(values):
            params = dict(zip(keys, combo, strict=False))
            score = self._evaluate(strategy_trades, params)
            if score is not None:
                results.append(score)

        results.sort(key=lambda r: r["score"], reverse=True)
        return results

    def _evaluate(self, trades: list[TradeRecord], params: dict[str, Any]) -> StatsDict | None:
        analyzer = TradeAnalyzer(trades)
        stats = analyzer.overall()
        if stats["total_trades"] == 0:
            return None

        score = 0.0
        score += stats["win_rate"] * 0.3
        score += (stats["profit_factor"] if stats["profit_factor"] != float("inf") else 10) * 10
        score += stats["sharpe"] * 5
        score += (stats["total_pnl"] / 1000)

        return {
            "params": params,
            "score": round(score, 4),
            "win_rate": stats["win_rate"],
            "total_pnl": stats["total_pnl"],
            "sharpe": stats["sharpe"],
            "total_trades": stats["total_trades"],
        }

    @staticmethod
    def _product(lists: list[list[float]]) -> list[tuple[float, ...]]:
        if not lists:
            return [()]
        result: list[tuple[float, ...]] = [()]
        for arg in lists:
            result = [prefix + (item,) for prefix in result for item in arg]
        return result
