"""Core endpoints: service info, health and readiness probes."""

from __future__ import annotations

import os
import time
from importlib.metadata import PackageNotFoundError, version as package_version

from flask import Blueprint, current_app, jsonify, url_for

bp = Blueprint("core", __name__)

STARTED_AT = time.time()
STARTED_AT_MONOTONIC = time.monotonic()

try:
    APP_VERSION = package_version("flaskbackendtest")
except PackageNotFoundError:  # running from a source checkout without install
    APP_VERSION = "0.0.0+unknown"


def _uptime_seconds() -> float:
    return round(time.monotonic() - STARTED_AT_MONOTONIC, 3)


def _routes() -> list[dict[str, object]]:
    routes = []
    for rule in current_app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        view = current_app.view_functions[rule.endpoint]
        doc = (view.__doc__ or "").strip().splitlines()
        routes.append(
            {
                "rule": rule.rule,
                "methods": sorted(rule.methods - {"HEAD", "OPTIONS"}),
                "description": doc[0] if doc else "",
            }
        )
    return sorted(routes, key=lambda item: str(item["rule"]))


@bp.get("/")
def index():
    """Service overview with the list of available endpoints."""
    return jsonify(
        {
            "name": current_app.config["APP_NAME"],
            "version": APP_VERSION,
            "environment": current_app.config["APP_ENV"],
            "instance": current_app.config["INSTANCE_ID"],
            "docs": {
                "hint": "Point your reverse proxy at this service and call "
                "/api/echo to see exactly what arrives at the backend.",
                "endpoints": _routes(),
            },
            "self_url": url_for("core.index", _external=True),
        }
    )


@bp.get("/healthz")
def healthz():
    """Liveness probe: is the process up and serving?"""
    return jsonify({"status": "ok", "uptime_seconds": _uptime_seconds()})


@bp.get("/readyz")
def readyz():
    """Readiness probe: may this instance receive traffic?"""
    # This backend has no external dependencies. Real services add their
    # database/cache/queue checks here and return 503 when one is down.
    checks = {"application": "ok"}
    ready = all(state == "ok" for state in checks.values())
    payload = {
        "status": "ready" if ready else "not_ready",
        "checks": checks,
        "uptime_seconds": _uptime_seconds(),
    }
    return jsonify(payload), (200 if ready else 503)


@bp.get("/version")
def version():
    """Build and runtime version information."""
    return jsonify(
        {
            "name": current_app.config["APP_NAME"],
            "version": APP_VERSION,
            "environment": current_app.config["APP_ENV"],
            "instance": current_app.config["INSTANCE_ID"],
            "pid": os.getpid(),
            "started_at": STARTED_AT,
            "uptime_seconds": _uptime_seconds(),
        }
    )
