"""Uniform JSON error responses.

A proxy in front of this app should never receive an HTML error page from a
JSON API, and internal exception details must not leak to clients.
"""

from __future__ import annotations

import logging

from flask import Flask, Response, g, jsonify, request
from werkzeug.exceptions import HTTPException

logger = logging.getLogger("flaskbackendtest.errors")


def error_response(status: int, error: str, message: str) -> Response:
    payload = {
        "error": error,
        "message": message,
        "status": status,
        "request_id": g.get("request_id"),
        "path": request.path,
    }
    response = jsonify(payload)
    response.status_code = status
    return response


def init_error_handlers(app: Flask) -> None:
    @app.errorhandler(HTTPException)
    def _handle_http_exception(exc: HTTPException) -> Response:
        status = exc.code or 500
        if status >= 500:
            logger.error(
                "http_error",
                extra={
                    "request_id": g.get("request_id"),
                    "status": status,
                    "path": request.path,
                },
                exc_info=exc,
            )
        response = error_response(
            status,
            exc.name.lower().replace(" ", "_"),
            exc.description or exc.name,
        )
        # Preserve headers the exception itself carries (e.g. Allow on a 405,
        # WWW-Authenticate on a 401) so proxies and clients still see them.
        for key, value in exc.get_headers():
            if key.lower() not in {"content-type", "content-length"}:
                response.headers.setdefault(key, value)
        return response

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception) -> Response:
        logger.exception(
            "unhandled_exception",
            extra={
                "request_id": g.get("request_id"),
                "path": request.path,
                "method": request.method,
            },
        )
        return error_response(
            500,
            "internal_server_error",
            "The server encountered an internal error and could not complete "
            "your request.",
        )
