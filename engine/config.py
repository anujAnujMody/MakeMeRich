import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class EngineConfig:
    openalgo_host: str = "http://localhost:5000"
    interval_minutes: int = 5
    dry_run: bool = True
    market_open_hour: int = 9
    market_open_minute: int = 15
    market_close_hour: int = 15
    market_close_minute: int = 30
    db_path: Path = field(default_factory=lambda: Path("data") / "trades.db")
    max_positions: int = 5


def load_config() -> EngineConfig:
    host = os.environ.get("OPENALGO_HOST", "http://localhost:5000")
    interval = int(os.environ.get("ENGINE_INTERVAL_MINUTES", "5"))
    dry_run = os.environ.get("ENGINE_DRY_RUN", "true").lower() == "true"
    max_pos = int(os.environ.get("ENGINE_MAX_POSITIONS", "5"))

    db_path_str = os.environ.get("ENGINE_DB_PATH", "")
    db_path = Path(db_path_str) if db_path_str else Path("data") / "trades.db"

    open_str = os.environ.get("ENGINE_MARKET_OPEN", "09:15")
    close_str = os.environ.get("ENGINE_MARKET_CLOSE", "15:30")

    open_h, open_m = (int(x) for x in open_str.split(":"))
    close_h, close_m = (int(x) for x in close_str.split(":"))

    return EngineConfig(
        openalgo_host=host,
        interval_minutes=interval,
        dry_run=dry_run,
        market_open_hour=open_h,
        market_open_minute=open_m,
        market_close_hour=close_h,
        market_close_minute=close_m,
        db_path=db_path,
        max_positions=max_pos,
    )
