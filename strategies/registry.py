from collections.abc import Callable
from typing import TYPE_CHECKING

from .config import STRATEGIES, InstrumentConfig, StrategyConfig

if TYPE_CHECKING:
    from .base import StrategyBase


_registry: dict[str, type["StrategyBase"]] = {}


def register(name: str) -> Callable[[type["StrategyBase"]], type["StrategyBase"]]:
    def decorator(cls: type["StrategyBase"]) -> type["StrategyBase"]:
        _registry[name] = cls
        return cls
    return decorator


def get_strategy(name: str, symbol: str, exchange: str) -> "StrategyBase | None":
    cls = _registry.get(name)
    if cls is None:
        return None
    return cls(name, symbol, exchange)


def list_strategies() -> list[dict[str, str | bool | list[str]]]:
    return [
        {
            "name": name,
            "enabled": cfg.enabled,
            "instruments": [i.symbol for i in cfg.instruments],
        }
        for name, cfg in STRATEGIES.items()
    ]


def get_config(name: str) -> StrategyConfig | None:
    return STRATEGIES.get(name)


def get_instruments(name: str) -> list[InstrumentConfig]:
    cfg = STRATEGIES.get(name)
    return list(cfg.instruments) if cfg else []
