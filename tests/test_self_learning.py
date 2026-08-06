# mypy: ignore-errors
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from engine.self_learning import ParamOptimizer, TradeAnalyzer
from strategies.base import ExitReason, TradeDirection, TradeOutcome, TradeRecord

IST = timezone(timedelta(hours=5, minutes=30))


def sample_trades() -> list[TradeRecord]:
    return [
        TradeRecord(
            id="t1", strategy="orbs", symbol="BANKNIFTY", exchange="NFO",
            direction=TradeDirection.LONG,
            entry_time=datetime(2024, 1, 2, 9, 30, tzinfo=IST),
            entry_price=45000.0,
            exit_time=datetime(2024, 1, 2, 10, 0, tzinfo=IST),
            exit_price=45300.0, quantity=15, lot_size=15,
            sl_price=44900.0, target_price=45300.0,
            exit_reason=ExitReason.TARGET, pnl=4500.0, outcome=TradeOutcome.WIN,
            range_high=45100.0, range_low=44900.0,
        ),
        TradeRecord(
            id="t2", strategy="orbs", symbol="NIFTY", exchange="NFO",
            direction=TradeDirection.SHORT,
            entry_time=datetime(2024, 1, 2, 9, 45, tzinfo=IST),
            entry_price=24100.0,
            exit_time=datetime(2024, 1, 2, 10, 15, tzinfo=IST),
            exit_price=24200.0, quantity=25, lot_size=25,
            sl_price=24250.0, target_price=24000.0,
            exit_reason=ExitReason.STOP_LOSS, pnl=-2500.0, outcome=TradeOutcome.LOSS,
            range_high=24200.0, range_low=24050.0,
        ),
        TradeRecord(
            id="t3", strategy="orbs", symbol="BANKNIFTY", exchange="NFO",
            direction=TradeDirection.LONG,
            entry_time=datetime(2024, 1, 3, 9, 30, tzinfo=IST),
            entry_price=45200.0,
            exit_time=datetime(2024, 1, 3, 10, 30, tzinfo=IST),
            exit_price=45500.0, quantity=15, lot_size=15,
            sl_price=45100.0, target_price=45400.0,
            exit_reason=ExitReason.TARGET, pnl=4500.0, outcome=TradeOutcome.WIN,
            range_high=45300.0, range_low=45100.0,
        ),
        TradeRecord(
            id="t4", strategy="macross", symbol="BANKNIFTY", exchange="NFO",
            direction=TradeDirection.LONG,
            entry_time=datetime(2024, 1, 3, 11, 0, tzinfo=IST),
            entry_price=45300.0,
            exit_time=datetime(2024, 1, 3, 11, 30, tzinfo=IST),
            exit_price=45200.0, quantity=10, lot_size=10,
            sl_price=45100.0, target_price=45600.0,
            exit_reason=ExitReason.STOP_LOSS, pnl=-1000.0, outcome=TradeOutcome.LOSS,
            range_high=45400.0, range_low=45200.0,
        ),
    ]


class TestTradeAnalyzer:
    def test_overall_win_rate(self) -> None:
        analyzer = TradeAnalyzer(sample_trades())
        stats = analyzer.overall()
        assert stats["total_trades"] == 4
        assert stats["win_rate"] == 50.0  # 2 wins / 4 total
        assert stats["total_pnl"] == 5500.0  # 4500 - 2500 + 4500 - 1000

    def test_overall_metrics(self) -> None:
        analyzer = TradeAnalyzer(sample_trades())
        stats = analyzer.overall()
        assert stats["profit_factor"] == 2.57  # 9000 / 3500
        assert stats["avg_win"] == 4500.0
        assert stats["avg_loss"] == -1750.0
        assert stats["max_drawdown"] < 0

    def test_per_strategy_stats(self) -> None:
        analyzer = TradeAnalyzer(sample_trades())
        by_strat = analyzer.by_strategy()
        assert "orbs" in by_strat
        assert "macross" in by_strat
        assert by_strat["orbs"]["total_trades"] == 3
        assert by_strat["orbs"]["win_rate"] == 66.67
        assert by_strat["macross"]["total_trades"] == 1
        assert by_strat["macross"]["win_rate"] == 0.0

    def test_per_symbol_stats(self) -> None:
        analyzer = TradeAnalyzer(sample_trades())
        by_sym = analyzer.by_symbol()
        assert "BANKNIFTY" in by_sym
        assert "NIFTY" in by_sym
        assert by_sym["BANKNIFTY"]["total_trades"] == 3
        assert by_sym["NIFTY"]["total_trades"] == 1

    def test_by_hour_breakdown(self) -> None:
        analyzer = TradeAnalyzer(sample_trades())
        by_hour = analyzer.by_hour()
        assert 9 in by_hour  # 3 trades at 9:xx
        assert 11 in by_hour  # 1 trade at 11:xx
        assert by_hour[9]["total_trades"] == 3
        assert by_hour[9]["win_rate"] == 66.67

    def test_by_day_breakdown(self) -> None:
        analyzer = TradeAnalyzer(sample_trades())
        by_day = analyzer.by_day()
        assert "Tuesday" in by_day  # Jan 2, 2024
        assert "Wednesday" in by_day  # Jan 3, 2024

    def test_empty_trades(self) -> None:
        analyzer = TradeAnalyzer([])
        stats = analyzer.overall()
        assert stats["total_trades"] == 0
        assert stats["win_rate"] == 0.0
        assert stats["total_pnl"] == 0.0

    def test_sharpe_ratio(self) -> None:
        analyzer = TradeAnalyzer(sample_trades())
        stats = analyzer.overall()
        assert "sharpe" in stats

    def test_consecutive_win_loss(self) -> None:
        analyzer = TradeAnalyzer(sample_trades())
        streaks = analyzer.streaks()
        assert streaks["max_consecutive_wins"] == 1
        assert streaks["max_consecutive_losses"] == 1


class TestParamOptimizer:
    def test_single_param_grid(self) -> None:
        trades = sample_trades()
        param_grid = {"target_rr": [1.0, 1.5, 2.0]}
        optimizer = ParamOptimizer(trades)
        results = optimizer.grid_search("orbs", param_grid)
        assert len(results) > 0
        for r in results:
            assert "params" in r
            assert "target_rr" in r["params"]
            assert "score" in r
            assert "win_rate" in r
            assert "total_pnl" in r

    def test_results_ranked_by_score(self) -> None:
        trades = sample_trades()
        param_grid = {"target_rr": [1.0, 1.5, 2.0]}
        optimizer = ParamOptimizer(trades)
        results = optimizer.grid_search("orbs", param_grid)
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_multi_param_grid(self) -> None:
        trades = sample_trades()
        param_grid = {
            "target_rr": [1.0, 1.5],
            "sl_rr": [0.5, 1.0],
        }
        optimizer = ParamOptimizer(trades)
        results = optimizer.grid_search("orbs", param_grid)
        assert len(results) == 4  # 2 * 2 combinations

    def test_no_matching_trades_returns_empty(self) -> None:
        trades = sample_trades()
        optimizer = ParamOptimizer(trades)
        results = optimizer.grid_search("nonexistent", {"target_rr": [1.0]})
        assert results == []

    def test_insufficient_trades_returns_empty(self) -> None:
        trades = [sample_trades()[0]]
        optimizer = ParamOptimizer(trades)
        results = optimizer.grid_search("orbs", {"target_rr": [1.0]})
        assert results == []


class TestConfig:
    def test_engine_config_has_self_learning_fields(self) -> None:
        from engine.config import EngineConfig

        cfg = EngineConfig()
        assert hasattr(cfg, "api_host")
        assert hasattr(cfg, "api_port")
        assert hasattr(cfg, "min_trades_for_analysis")
