"""Tests for te.settings.Settings — required env/database_url, cross-env guard."""

import pytest
from pydantic import ValidationError

from te.settings import Settings


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clears any TE_* vars that might leak in from the real environment."""
    for key in list(__import__("os").environ):
        if key.startswith("TE_"):
            monkeypatch.delenv(key, raising=False)


def test_settings_fails_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("TE_ENV", "dev")
    monkeypatch.setenv("TE_BAR_STORE_PATH", "data/bars")
    monkeypatch.setenv("TE_OPENALGO_HOST", "http://openalgo:5000")
    monkeypatch.setenv("TE_OPENALGO_API_KEY", "secret")
    monkeypatch.setenv("TE_CORS_ORIGINS", '["http://localhost:5173"]')
    with pytest.raises(ValidationError):
        Settings()


def test_settings_fails_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("TE_DATABASE_URL", "sqlite:///dev.db")
    monkeypatch.setenv("TE_BAR_STORE_PATH", "data/bars")
    monkeypatch.setenv("TE_OPENALGO_HOST", "http://openalgo:5000")
    monkeypatch.setenv("TE_OPENALGO_API_KEY", "secret")
    monkeypatch.setenv("TE_CORS_ORIGINS", '["http://localhost:5173"]')
    with pytest.raises(ValidationError):
        Settings()


def test_cross_env_guard_dev_prod_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("TE_ENV", "dev")
    monkeypatch.setenv("TE_DATABASE_URL", "sqlite:///prod.db")
    monkeypatch.setenv("TE_BAR_STORE_PATH", "data/bars")
    monkeypatch.setenv("TE_OPENALGO_HOST", "http://openalgo:5000")
    monkeypatch.setenv("TE_OPENALGO_API_KEY", "secret")
    monkeypatch.setenv("TE_CORS_ORIGINS", '["http://localhost:5173"]')
    with pytest.raises(ValidationError):
        Settings()


def test_cross_env_guard_prod_dev_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("TE_ENV", "prod")
    monkeypatch.setenv("TE_DATABASE_URL", "sqlite:///dev.db")
    monkeypatch.setenv("TE_BAR_STORE_PATH", "data/bars")
    monkeypatch.setenv("TE_OPENALGO_HOST", "http://openalgo:5000")
    monkeypatch.setenv("TE_OPENALGO_API_KEY", "secret")
    monkeypatch.setenv("TE_CORS_ORIGINS", '["http://localhost:5173"]')
    with pytest.raises(ValidationError):
        Settings()


def test_settings_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("TE_ENV", "dev")
    monkeypatch.setenv("TE_DATABASE_URL", "sqlite:///dev.db")
    monkeypatch.setenv("TE_BAR_STORE_PATH", "data/bars")
    monkeypatch.setenv("TE_OPENALGO_HOST", "http://openalgo:5000")
    monkeypatch.setenv("TE_OPENALGO_WS_HOST", "ws://openalgo:8765")
    monkeypatch.setenv("TE_OPENALGO_API_KEY", "secret")
    monkeypatch.setenv("TE_CORS_ORIGINS", '["http://localhost:5173"]')

    settings = Settings()

    assert settings.env == "dev"
    assert settings.database_url == "sqlite:///dev.db"
    assert settings.bar_store_path.as_posix() == "data/bars"
    assert settings.openalgo_host == "http://openalgo:5000"
    assert settings.openalgo_ws_host == "ws://openalgo:8765"
    assert settings.openalgo_api_key.get_secret_value() == "secret"
    assert settings.cors_origins == ["http://localhost:5173"]
    assert settings.charges_path.as_posix() == "config/charges.yaml"
    assert settings.max_orders_per_second == 5


def test_settings_fails_without_openalgo_ws_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The WS endpoint is required, never derived from `openalgo_host`. The
    REST host already carries its own port (`http://openalgo:5000`), so any
    string-surgery derivation produces a two-port, invalid URL
    (`ws://openalgo:5000:8765`); and in production the REST and WS endpoints
    may be entirely different hosts. A missing value must fail loudly at
    startup rather than silently never connecting."""
    _base_env(monkeypatch)
    monkeypatch.setenv("TE_ENV", "dev")
    monkeypatch.setenv("TE_DATABASE_URL", "sqlite:///dev.db")
    monkeypatch.setenv("TE_BAR_STORE_PATH", "data/bars")
    monkeypatch.setenv("TE_OPENALGO_HOST", "http://openalgo:5000")
    monkeypatch.setenv("TE_OPENALGO_API_KEY", "secret")
    monkeypatch.setenv("TE_CORS_ORIGINS", '["http://localhost:5173"]')
    with pytest.raises(ValidationError):
        Settings()
