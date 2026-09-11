"""Configuration loading.

All settings come from environment variables (12-factor style) so the same
image/artifact can run in every environment without code changes.
"""

from __future__ import annotations

import os
import secrets
import socket
from typing import Any

TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class ConfigError(RuntimeError):
    """Raised when the process is started with an invalid configuration."""


def env_str(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ConfigError(f"{name} must be a boolean value, got {raw!r}")


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def env_float(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def env_list(name: str, default: list[str] | None = None) -> list[str] | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_config() -> dict[str, Any]:
    """Build the Flask config mapping from the environment."""
    app_env = (env_str("APP_ENV", "development") or "development").lower()
    if app_env not in {"development", "testing", "production"}:
        raise ConfigError(
            f"APP_ENV must be one of development/testing/production, got {app_env!r}"
        )

    secret_key = env_str("SECRET_KEY")
    if secret_key is None:
        if app_env == "production":
            raise ConfigError("SECRET_KEY must be set when APP_ENV=production")
        # Ephemeral key: fine for local runs, sessions do not survive a restart.
        secret_key = secrets.token_urlsafe(32)

    log_format = (env_str("LOG_FORMAT", "json") or "json").lower()
    if log_format not in {"json", "text"}:
        raise ConfigError(f"LOG_FORMAT must be 'json' or 'text', got {log_format!r}")

    return {
        # --- application -------------------------------------------------
        "APP_NAME": env_str("APP_NAME", "flask-proxy-test-backend"),
        "APP_ENV": app_env,
        "INSTANCE_ID": env_str("INSTANCE_ID", socket.gethostname()),
        "SECRET_KEY": secret_key,
        "DEBUG": False,
        "TESTING": app_env == "testing",
        "PROPAGATE_EXCEPTIONS": False,
        # Reject bodies larger than this with 413 instead of buffering them.
        "MAX_CONTENT_LENGTH": env_int("MAX_CONTENT_LENGTH", 16 * 1024 * 1024, minimum=1),
        # Host header allow-list (Flask >= 3.1). Empty means "accept any Host".
        "TRUSTED_HOSTS": env_list("TRUSTED_HOSTS"),
        # --- reverse proxy ----------------------------------------------
        # Number of trusted proxies in front of this app. 0 disables the
        # rewrite for that header. Never trust more hops than you control.
        "PROXY_FIX_X_FOR": env_int("PROXY_FIX_X_FOR", 1, minimum=0),
        "PROXY_FIX_X_PROTO": env_int("PROXY_FIX_X_PROTO", 1, minimum=0),
        "PROXY_FIX_X_HOST": env_int("PROXY_FIX_X_HOST", 1, minimum=0),
        "PROXY_FIX_X_PORT": env_int("PROXY_FIX_X_PORT", 0, minimum=0),
        "PROXY_FIX_X_PREFIX": env_int("PROXY_FIX_X_PREFIX", 0, minimum=0),
        # --- logging ------------------------------------------------------
        "LOG_LEVEL": (env_str("LOG_LEVEL", "INFO") or "INFO").upper(),
        "LOG_FORMAT": log_format,
        "LOG_HEALTH_REQUESTS": env_bool("LOG_HEALTH_REQUESTS", False),
        "REQUEST_ID_HEADER": env_str("REQUEST_ID_HEADER", "X-Request-ID"),
        # --- responses -----------------------------------------------------
        "ENABLE_HSTS": env_bool("ENABLE_HSTS", False),
        "HSTS_MAX_AGE": env_int("HSTS_MAX_AGE", 31536000, minimum=0),
        # --- limits for the proxy test endpoints ---------------------------
        "MAX_DELAY_SECONDS": env_float("MAX_DELAY_SECONDS", 30.0, minimum=0.0),
        "MAX_PAYLOAD_BYTES": env_int("MAX_PAYLOAD_BYTES", 10 * 1024 * 1024, minimum=0),
        "MAX_STREAM_CHUNKS": env_int("MAX_STREAM_CHUNKS", 1000, minimum=1),
        "MAX_STREAM_SECONDS": env_float("MAX_STREAM_SECONDS", 120.0, minimum=0.0),
        "MAX_ECHO_BODY_BYTES": env_int("MAX_ECHO_BODY_BYTES", 64 * 1024, minimum=0),
    }
