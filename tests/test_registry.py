from typing import Any

from strategies.base import Signal, StrategyBase
from strategies.registry import get_strategy, list_strategies, register


class _MockStrategy(StrategyBase):
    def generate_signals(self, ohlcv: list[dict[str, Any]]) -> list[Signal]:
        return []


class TestRegisterAndGet:
    def test_register_and_get(self) -> None:
        register("mock_test")(_MockStrategy)
        instance = get_strategy("mock_test", "TEST", "NSE")
        assert instance is not None
        assert instance.symbol == "TEST"
        assert instance.exchange == "NSE"

    def test_get_unknown(self) -> None:
        assert get_strategy("does_not_exist", "X", "Y") is None

    def test_list_strategies_includes_orbs(self) -> None:
        strategies = list_strategies()
        names = [s["name"] for s in strategies]
        assert "orbs" in names

    def test_register_decorator(self) -> None:
        @register("decorator_test")
        class DecoratedStrat(StrategyBase):
            def generate_signals(self, ohlcv: list[dict[str, Any]]) -> list[Signal]:
                return []

        instance = get_strategy("decorator_test", "D", "NSE")
        assert instance is not None
        assert instance.symbol == "D"
