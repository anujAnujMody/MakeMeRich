from strategies.base import ExitReason, TradeDirection, TradeOutcome
from strategies.orbs import ORBStrategy
from tests.conftest import make_bar


def _strategy() -> ORBStrategy:
    s = ORBStrategy("orbs", "TEST", "NSE")
    s.lot_size = 1
    s.min_range_pts = 0
    s.max_range_pts = 99999
    s.volume_multiplier = 1.5
    s.target_rr = 1.5
    s.sl_rr = 1.0
    return s


class TestRangeCalculation:
    def test_range_calc_3_candles(self) -> None:
        ohlcv = [
            make_bar(9, 15, 100, 105, 95, 101),
            make_bar(9, 20, 101, 150, 100, 145),
            make_bar(9, 25, 145, 150, 140, 145),
        ]
        s = _strategy()
        signals = s.generate_signals(ohlcv)
        assert s._range_high == 150
        assert s._range_low == 95
        assert signals == []


class TestBreakoutEntry:
    def test_long_close_breakout(self) -> None:
        ohlcv = [
            make_bar(9, 15, 100, 105, 95, 101),
            make_bar(9, 20, 101, 150, 100, 145),
            make_bar(9, 25, 145, 150, 140, 145),
            make_bar(9, 30, 145, 160, 140, 155, volume=20000),
        ]
        s = _strategy()
        signals = s.generate_signals(ohlcv)
        assert len(signals) == 1
        assert signals[0].direction == TradeDirection.LONG

    def test_short_close_breakout(self) -> None:
        ohlcv = [
            make_bar(9, 15, 100, 105, 95, 101),
            make_bar(9, 20, 101, 150, 100, 145),
            make_bar(9, 25, 145, 150, 140, 145),
            make_bar(9, 30, 100, 105, 90, 92, volume=20000),
        ]
        s = _strategy()
        signals = s.generate_signals(ohlcv)
        assert len(signals) == 1
        assert signals[0].direction == TradeDirection.SHORT

    def test_wick_no_breakout(self) -> None:
        ohlcv = [
            make_bar(9, 15, 100, 105, 95, 101),
            make_bar(9, 20, 101, 150, 100, 145),
            make_bar(9, 25, 145, 150, 140, 145),
            make_bar(9, 30, 140, 160, 140, 148, volume=20000),
        ]
        s = _strategy()
        signals = s.generate_signals(ohlcv)
        assert signals == []

    def test_breakout_ignored_during_range(self) -> None:
        ohlcv = [
            make_bar(9, 15, 100, 160, 95, 155),
            make_bar(9, 20, 155, 160, 100, 145),
            make_bar(9, 25, 145, 150, 140, 145),
        ]
        s = _strategy()
        signals = s.generate_signals(ohlcv)
        assert signals == []


class TestVolumeGate:
    def test_volume_gate_rejects(self) -> None:
        ohlcv = [
            make_bar(9, 15, 100, 105, 95, 101, volume=10000),
            make_bar(9, 20, 101, 150, 100, 145, volume=10000),
            make_bar(9, 25, 145, 150, 140, 145, volume=10000),
            make_bar(9, 30, 145, 160, 140, 155, volume=12000),
        ]
        s = _strategy()
        signals = s.generate_signals(ohlcv)
        assert signals == []


class TestWidthFilter:
    def test_range_too_narrow(self) -> None:
        s = _strategy()
        s.min_range_pts = 20
        ohlcv = [
            make_bar(9, 15, 100, 105, 96, 101),
            make_bar(9, 20, 101, 110, 100, 105),
            make_bar(9, 25, 105, 110, 102, 105),
        ]
        signals = s.generate_signals(ohlcv)
        assert signals == []

    def test_range_too_wide(self) -> None:
        s = _strategy()
        s.max_range_pts = 100
        ohlcv = [
            make_bar(9, 15, 100, 300, 50, 200),
            make_bar(9, 20, 200, 300, 100, 250),
            make_bar(9, 25, 250, 300, 200, 250),
        ]
        signals = s.generate_signals(ohlcv)
        assert signals == []


class TestPerDirectionLimit:
    def test_one_long_per_day(self) -> None:
        s = _strategy()
        ohlcv = [
            make_bar(9, 15, 100, 105, 95, 101),
            make_bar(9, 20, 101, 150, 100, 145),
            make_bar(9, 25, 145, 150, 140, 145),
            make_bar(9, 30, 145, 160, 140, 155, volume=20000),
            make_bar(9, 35, 155, 170, 150, 165, volume=20000),
        ]
        first = s.generate_signals(ohlcv)
        assert len(first) == 1
        s.trades_today["LONG"] = 1
        second = s.generate_signals(ohlcv)
        assert second == []


class TestExitLogic:
    def test_sl_at_range_low_long(self) -> None:
        ohlcv = [
            make_bar(9, 15, 100, 105, 95, 101),
            make_bar(9, 20, 101, 150, 100, 145),
            make_bar(9, 25, 145, 150, 140, 145),
            make_bar(9, 30, 145, 160, 140, 155, volume=20000),
        ]
        s = _strategy()
        signals = s.generate_signals(ohlcv)
        assert len(signals) == 1
        assert signals[0].sl_price == 95

    def test_full_backtest_flow(self) -> None:
        s = _strategy()
        ohlcv = [
            make_bar(9, 15, 100, 105, 95, 101),
            make_bar(9, 20, 101, 150, 100, 145),
            make_bar(9, 25, 145, 150, 140, 145),
            make_bar(9, 30, 145, 160, 140, 155, volume=20000),
            make_bar(9, 35, 155, 240, 150, 235, volume=20000),
            make_bar(9, 40, 235, 240, 230, 238),
        ]
        trades = s.run_backtest(ohlcv)
        assert len(trades) == 1
        t = trades[0]
        assert t.direction == TradeDirection.LONG
        assert t.exit_reason == ExitReason.TARGET
        assert t.pnl > 0
        assert t.outcome == TradeOutcome.WIN
