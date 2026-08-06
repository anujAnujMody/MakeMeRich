from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InstrumentConfig:
    symbol: str
    exchange: str
    range_min: int = 15
    max_trades: int = 2
    lot_size: int = 1
    product: str = "NRML"


@dataclass(frozen=True)
class StrategyParams:
    target_rr: float = 1.5
    sl_rr: float = 1.0
    time_exit_hour: int = 15
    time_exit_minute: int = 15
    min_range_pts: int = 20
    max_range_pts: int = 200
    volume_multiplier: float = 1.5
    max_per_direction: int = 1


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    enabled: bool
    instruments: list[InstrumentConfig]
    params: StrategyParams


STRATEGIES: dict[str, StrategyConfig] = {
    "orbs": StrategyConfig(
        name="orbs",
        enabled=True,
        instruments=[
            InstrumentConfig(symbol="BANKNIFTY", exchange="NFO", lot_size=15),
            InstrumentConfig(symbol="NIFTY", exchange="NFO", lot_size=25),
            InstrumentConfig(symbol="FINNIFTY", exchange="NFO", range_min=20, max_trades=1, lot_size=25),
            InstrumentConfig(symbol="SENSEX", exchange="BFO", lot_size=10),
        ],
        params=StrategyParams(
            target_rr=1.5,
            sl_rr=1.0,
            time_exit_hour=15,
            time_exit_minute=15,
            min_range_pts=20,
            max_range_pts=200,
            volume_multiplier=1.5,
            max_per_direction=1,
        ),
    ),
}

RISK: dict[str, Any] = {
    "max_loss_per_trade": 150,
    "max_loss_per_day": 300,
    "max_consecutive_losses": 3,
    "capital": 10_000,
    "max_positions_per_instrument": 2,
    "no_trade_after_hour": 11,
    "expiry_blacklist_days": ["Friday"],
}

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def get_config(name: str) -> StrategyConfig | None:
    return STRATEGIES.get(name)


def get_instruments(name: str) -> list[InstrumentConfig]:
    cfg = get_config(name)
    return list(cfg.instruments) if cfg else []
