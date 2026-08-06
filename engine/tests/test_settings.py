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


def _full_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("TE_ENV", "dev")
    monkeypatch.setenv("TE_DATABASE_URL", "sqlite:///dev.db")
    monkeypatch.setenv("TE_BAR_STORE_PATH", "data/bars")
    monkeypatch.setenv("TE_OPENALGO_HOST", "http://openalgo:5000")
    monkeypatch.setenv("TE_OPENALGO_WS_HOST", "ws://openalgo:8765")
    monkeypatch.setenv("TE_OPENALGO_API_KEY", "secret")
    monkeypatch.setenv("TE_CORS_ORIGINS", '["http://localhost:5173"]')


def test_relogin_fields_default_to_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _full_env(monkeypatch)
    settings = Settings()
    assert settings.openalgo_app_username is None
    assert settings.openalgo_app_password is None
    assert settings.angel_client_id is None
    assert settings.angel_pin is None
    assert settings.angel_totp_secret is None


def test_relogin_fields_treat_docker_composes_blank_substitution_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: `docker-compose.yml`'s `${OPENALGO_APP_USERNAME:-}` passes
    an EMPTY STRING (not an unset var) when the root `.env` doesn't define
    it — which would otherwise satisfy `str | None` as `""` rather than
    `None`, silently defeating `run_openalgo_relogin`'s all-or-nothing
    "not configured" skip (it would see 5 present-but-blank values and
    attempt a real broker login with empty credentials)."""
    _full_env(monkeypatch)
    monkeypatch.setenv("TE_OPENALGO_APP_USERNAME", "")
    monkeypatch.setenv("TE_ANGEL_PIN", "")
    settings = Settings()
    assert settings.openalgo_app_username is None
    assert settings.angel_pin is None


def test_relogin_fields_populate_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _full_env(monkeypatch)
    monkeypatch.setenv("TE_OPENALGO_APP_USERNAME", "app-user")
    monkeypatch.setenv("TE_OPENALGO_APP_PASSWORD", "app-pass")
    monkeypatch.setenv("TE_ANGEL_CLIENT_ID", "C123")
    monkeypatch.setenv("TE_ANGEL_PIN", "1234")
    monkeypatch.setenv("TE_ANGEL_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    settings = Settings()
    assert settings.openalgo_app_username == "app-user"
    assert settings.openalgo_app_password is not None
    assert settings.openalgo_app_password.get_secret_value() == "app-pass"
    assert settings.angel_client_id == "C123"
    assert settings.angel_pin is not None
    assert settings.angel_pin.get_secret_value() == "1234"
    assert settings.angel_totp_secret is not None
    assert settings.angel_totp_secret.get_secret_value() == "JBSWY3DPEHPK3PXP"
