"""A small, production-ready Flask backend for reverse proxy testing."""

from __future__ import annotations

from typing import Any

from flask import Flask

from flaskbackendtest.config import load_config
from flaskbackendtest.errors import init_error_handlers
from flaskbackendtest.logging_setup import configure_logging
from flaskbackendtest.middleware import init_proxy_fix, init_request_hooks

__all__ = ["create_app"]


def create_app(overrides: dict[str, Any] | None = None) -> Flask:
    """Build and configure the WSGI application.

    Args:
        overrides: Config values applied after the environment, used by tests.
    """
    app = Flask(__name__)
    app.config.from_mapping(load_config())
    if overrides:
        app.config.from_mapping(overrides)

    configure_logging(app.config["LOG_LEVEL"], app.config["LOG_FORMAT"])

    # ProxyFix must wrap the app before any request is served: it rewrites the
    # WSGI environ so remote_addr/scheme/host reflect the original client.
    init_proxy_fix(app)
    init_request_hooks(app)
    init_error_handlers(app)

    from flaskbackendtest.api import bp as api_bp
    from flaskbackendtest.routes import bp as core_bp

    app.register_blueprint(core_bp)
    app.register_blueprint(api_bp)

    app.logger.info(
        "application_started",
        extra={
            "app_name": app.config["APP_NAME"],
            "environment": app.config["APP_ENV"],
            "instance": app.config["INSTANCE_ID"],
            "proxy_fix_x_for": app.config["PROXY_FIX_X_FOR"],
            "proxy_fix_x_proto": app.config["PROXY_FIX_X_PROTO"],
            "proxy_fix_x_host": app.config["PROXY_FIX_X_HOST"],
            "trusted_hosts": app.config["TRUSTED_HOSTS"],
        },
    )
    return app
