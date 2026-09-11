"""Request-scoped plumbing: proxy header handling, request IDs, access log."""

from __future__ import annotations

import logging
import re
import time
import uuid

from flask import Flask, Response, current_app, g, request
from werkzeug.middleware.proxy_fix import ProxyFix

logger = logging.getLogger("flaskbackendtest.access")

HEALTH_PATHS = frozenset({"/healthz", "/readyz"})
# Request IDs end up in logs, so only accept a conservative character set.
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:@+-]{1,128}$")


def init_proxy_fix(app: Flask) -> None:
    """Trust X-Forwarded-* headers from the configured number of proxies.

    Without this the app sees the proxy's IP as the client and always thinks
    it is speaking plain HTTP, which breaks redirects and external URLs.
    """
    x_for = app.config["PROXY_FIX_X_FOR"]
    x_proto = app.config["PROXY_FIX_X_PROTO"]
    x_host = app.config["PROXY_FIX_X_HOST"]
    x_port = app.config["PROXY_FIX_X_PORT"]
    x_prefix = app.config["PROXY_FIX_X_PREFIX"]

    if not any((x_for, x_proto, x_host, x_port, x_prefix)):
        app.logger.info("proxy_fix_disabled")
        return

    app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
        app.wsgi_app,
        x_for=x_for,
        x_proto=x_proto,
        x_host=x_host,
        x_port=x_port,
        x_prefix=x_prefix,
    )


def resolve_request_id(header_name: str) -> str:
    """Reuse the proxy's request ID when it looks sane, else mint a new one."""
    incoming = request.headers.get(header_name, "")
    if REQUEST_ID_RE.match(incoming):
        return incoming
    return uuid.uuid4().hex


def forwarded_chain() -> list[str]:
    raw = request.headers.get("X-Forwarded-For", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def init_request_hooks(app: Flask) -> None:
    @app.before_request
    def _begin_request() -> None:
        g.request_start = time.perf_counter()
        g.request_id = resolve_request_id(current_app.config["REQUEST_ID_HEADER"])

    @app.after_request
    def _finish_request(response: Response) -> Response:
        request_id = g.get("request_id")
        if request_id:
            response.headers[current_app.config["REQUEST_ID_HEADER"]] = request_id

        started = g.get("request_start")
        duration_ms = (
            round((time.perf_counter() - started) * 1000, 3) if started else None
        )
        if duration_ms is not None:
            response.headers["X-Response-Time-Ms"] = f"{duration_ms:.3f}"

        response.headers["X-Served-By"] = current_app.config["INSTANCE_ID"]
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if current_app.config["ENABLE_HSTS"] and request.is_secure:
            max_age = current_app.config["HSTS_MAX_AGE"]
            response.headers.setdefault(
                "Strict-Transport-Security", f"max-age={max_age}; includeSubDomains"
            )

        if request.path in HEALTH_PATHS and not current_app.config[
            "LOG_HEALTH_REQUESTS"
        ]:
            return response

        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.path,
                "query": request.query_string.decode("latin-1"),
                "status": response.status_code,
                "duration_ms": duration_ms,
                "remote_addr": request.remote_addr,
                "forwarded_for": forwarded_chain(),
                "scheme": request.scheme,
                "host": request.host,
                "user_agent": request.headers.get("User-Agent"),
                "content_length": request.content_length,
            },
        )
        return response
