import os

# Baseline TE_* settings for the test session, so `te.api.main` (which builds
# `Settings()` eagerly at import time) can be imported without every test
# module having to set env vars itself. `tests/test_settings.py` monkeypatches
# its own values per-test and pytest's monkeypatch fixture restores these
# afterwards, so this doesn't leak into settings-specific assertions.
os.environ.setdefault("TE_ENV", "dev")
os.environ.setdefault("TE_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("TE_BAR_STORE_PATH", "data/bars")
os.environ.setdefault("TE_OPENALGO_HOST", "http://openalgo:5000")
os.environ.setdefault("TE_OPENALGO_WS_HOST", "ws://openalgo:8765")
os.environ.setdefault("TE_OPENALGO_API_KEY", "test-key")
os.environ.setdefault("TE_CORS_ORIGINS", '["http://localhost:5173"]')
