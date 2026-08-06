# mypy: ignore-errors
from pathlib import Path

import pytest

from engine.config import EngineConfig, load_config


class TestEngineConfigDefaults:
    def test_default_host(self) -> None:
        cfg = EngineConfig()
        assert cfg.openalgo_host == "http://localhost:5000"

    def test_default_interval(self) -> None:
        cfg = EngineConfig()
        assert cfg.interval_minutes == 5

    def test_default_dry_run(self) -> None:
        cfg = EngineConfig()
        assert cfg.dry_run is True

    def test_default_market_open(self) -> None:
        cfg = EngineConfig()
        assert cfg.market_open_hour == 9
        assert cfg.market_open_minute == 15

    def test_default_market_close(self) -> None:
        cfg = EngineConfig()
        assert cfg.market_close_hour == 15
        assert cfg.market_close_minute == 30

    def test_default_db_path(self) -> None:
        cfg = EngineConfig()
        assert cfg.db_path == Path("data") / "trades.db"

    def test_default_max_positions(self) -> None:
        cfg = EngineConfig()
        assert cfg.max_positions == 5


class TestLoadConfig:
    def test_load_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENALGO_HOST", "http://192.168.1.100:5000")
        monkeypatch.setenv("ENGINE_INTERVAL_MINUTES", "10")
        monkeypatch.setenv("ENGINE_DRY_RUN", "false")
        monkeypatch.setenv("ENGINE_MAX_POSITIONS", "3")

        cfg = load_config()
        assert cfg.openalgo_host == "http://192.168.1.100:5000"
        assert cfg.interval_minutes == 10
        assert cfg.dry_run is False
        assert cfg.max_positions == 3

    def test_env_db_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENGINE_DB_PATH", "/custom/path/trades.db")
        cfg = load_config()
        assert cfg.db_path == Path("/custom/path/trades.db")

    def test_env_market_hours(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENGINE_MARKET_OPEN", "10:00")
        monkeypatch.setenv("ENGINE_MARKET_CLOSE", "14:30")
        cfg = load_config()
        assert cfg.market_open_hour == 10
        assert cfg.market_open_minute == 0
        assert cfg.market_close_hour == 14
        assert cfg.market_close_minute == 30

    def test_env_unset_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENALGO_HOST", raising=False)
        monkeypatch.delenv("ENGINE_INTERVAL_MINUTES", raising=False)
        cfg = load_config()
        assert cfg.openalgo_host == "http://localhost:5000"
        assert cfg.interval_minutes == 5
