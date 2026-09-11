from __future__ import annotations

import pytest

from flaskbackendtest import create_app

BASE_ENV = {
    "APP_ENV": "testing",
    "SECRET_KEY": "test-secret",
    "LOG_FORMAT": "text",
    "LOG_LEVEL": "WARNING",
    "INSTANCE_ID": "test-instance",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from the developer's shell environment."""
    for key in list(BASE_ENV) + [
        "PROXY_FIX_X_FOR",
        "PROXY_FIX_X_PROTO",
        "PROXY_FIX_X_HOST",
        "TRUSTED_HOSTS",
        "MAX_DELAY_SECONDS",
        "MAX_PAYLOAD_BYTES",
    ]:
        monkeypatch.delenv(key, raising=False)
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def app_factory():
    """Build an app with explicit config overrides."""
    return create_app
