"""Endpoints for exercising a reverse proxy.

Every handler answers one concrete question about the proxy in front of it:
which client the backend sees, whether forwarded headers arrive, how the proxy
treats slow responses, streaming, large bodies, redirects, cookies and error
status codes.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Iterator
from typing import Any

from flask import (
    Blueprint,
    Response,
    current_app,
    g,
    jsonify,
    redirect,
    request,
    stream_with_context,
    url_for,
)
from werkzeug.exceptions import BadRequest

from flaskbackendtest.middleware import forwarded_chain

bp = Blueprint("api", __name__, url_prefix="/api")

FORM_MIMETYPES = frozenset(
    {"application/x-www-form-urlencoded", "multipart/form-data"}
)
# Status codes that must not carry a body (RFC 9110).
BODYLESS_STATUSES = frozenset({204, 205, 304})
COOKIE_SAMESITE_VALUES = frozenset({"Strict", "Lax", "None"})


def _headers() -> dict[str, Any]:
    """Request headers; repeated headers become a list.

    Note that WSGI folds header names into a canonical form, so the original
    casing sent on the wire is not preserved.
    """
    result: dict[str, Any] = {}
    for key in request.headers.keys():
        values = request.headers.getlist(key)
        result[key] = values[0] if len(values) == 1 else values
    return result


def _query_params() -> dict[str, Any]:
    return {
        key: (values[0] if len(values) == 1 else values)
        for key, values in request.args.lists()
    }


def _proxy_view() -> dict[str, Any]:
    """What the backend concludes about the client after ProxyFix ran."""
    return {
        "remote_addr": request.remote_addr,
        "scheme": request.scheme,
        "is_secure": request.is_secure,
        "host": request.host,
        "script_root": request.script_root,
        "http_version": request.environ.get("SERVER_PROTOCOL"),
        "forwarded_for": forwarded_chain(),
        "forwarded_proto": request.headers.get("X-Forwarded-Proto"),
        "forwarded_host": request.headers.get("X-Forwarded-Host"),
        "forwarded_port": request.headers.get("X-Forwarded-Port"),
        "forwarded_prefix": request.headers.get("X-Forwarded-Prefix"),
        "forwarded": request.headers.get("Forwarded"),
        "via": request.headers.get("Via"),
        "behind_proxy": bool(
            forwarded_chain() or request.headers.get("Forwarded")
        ),
        "trusted_hops": {
            "x_for": current_app.config["PROXY_FIX_X_FOR"],
            "x_proto": current_app.config["PROXY_FIX_X_PROTO"],
            "x_host": current_app.config["PROXY_FIX_X_HOST"],
            "x_port": current_app.config["PROXY_FIX_X_PORT"],
            "x_prefix": current_app.config["PROXY_FIX_X_PREFIX"],
        },
    }


def _body() -> dict[str, Any]:
    """Describe the request body without ever returning invalid JSON."""
    limit = current_app.config["MAX_ECHO_BODY_BYTES"]
    info: dict[str, Any] = {
        "content_type": request.content_type,
        "content_length": request.content_length,
        "mimetype": request.mimetype or None,
    }

    if request.mimetype in FORM_MIMETYPES:
        # Touch the form parser first: it consumes the stream, so reading the
        # raw body afterwards would always come back empty.
        info["form"] = {
            key: (values[0] if len(values) == 1 else values)
            for key, values in request.form.lists()
        }
        info["files"] = [
            {
                "field": field,
                "filename": storage.filename,
                "content_type": storage.content_type,
            }
            for field, storage in request.files.items(multi=True)
        ]
        info["encoding"] = "form"
        return info

    raw = request.get_data(cache=True, parse_form_data=False)
    info["size_bytes"] = len(raw)
    info["truncated"] = len(raw) > limit
    body = raw[:limit]
    try:
        info["encoding"] = "utf-8"
        info["data"] = body.decode("utf-8")
    except UnicodeDecodeError:
        info["encoding"] = "base64"
        info["data"] = base64.b64encode(body).decode("ascii")

    if request.mimetype == "application/json" and raw and not info["truncated"]:
        try:
            info["json"] = json.loads(raw)
        except ValueError as exc:
            info["json_error"] = str(exc)
    return info


def _positive_int(name: str, default: int, maximum: int) -> int:
    raw = request.args.get(name)
    if raw is None or not raw.strip():
        return min(default, maximum)
    try:
        value = int(raw)
    except ValueError:
        raise BadRequest(f"Query parameter '{name}' must be an integer.") from None
    if value < 0:
        raise BadRequest(f"Query parameter '{name}' must not be negative.")
    return min(value, maximum)


def _positive_float(name: str, default: float, maximum: float) -> float:
    raw = request.args.get(name)
    if raw is None or not raw.strip():
        return min(default, maximum)
    try:
        value = float(raw)
    except ValueError:
        raise BadRequest(f"Query parameter '{name}' must be a number.") from None
    if value < 0:
        raise BadRequest(f"Query parameter '{name}' must not be negative.")
    return min(value, maximum)


@bp.route(
    "/echo", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
)
def echo():
    """Reflect the whole request as the backend received it."""
    return jsonify(
        {
            "request_id": g.get("request_id"),
            "served_by": current_app.config["INSTANCE_ID"],
            "received_at": time.time(),
            "method": request.method,
            "path": request.path,
            "full_path": request.full_path,
            "url": request.url,
            "base_url": request.base_url,
            "url_root": request.url_root,
            "query_params": _query_params(),
            "client": _proxy_view(),
            "headers": _headers(),
            "cookies": dict(request.cookies),
            "body": _body(),
        }
    )


@bp.get("/whoami")
def whoami():
    """Show which client and scheme the backend sees behind the proxy."""
    return jsonify(
        {
            "request_id": g.get("request_id"),
            "served_by": current_app.config["INSTANCE_ID"],
            "client": _proxy_view(),
        }
    )


@bp.get("/headers")
def headers():
    """List the request headers that reached the backend."""
    collected = _headers()
    return jsonify({"count": len(collected), "headers": collected})


@bp.get("/status/<int:code>")
def status(code: int):
    """Answer with an arbitrary status code (200-599)."""
    if not 200 <= code <= 599:
        raise BadRequest("Status code must be between 200 and 599.")
    if code in BODYLESS_STATUSES:
        return Response(status=code)
    payload = jsonify(
        {
            "requested_status": code,
            "request_id": g.get("request_id"),
            "served_by": current_app.config["INSTANCE_ID"],
        }
    )
    payload.status_code = code
    return payload


@bp.get("/delay/<seconds>")
def delay(seconds: str):
    """Respond after a delay, for proxy read-timeout testing."""
    try:
        requested = float(seconds)
    except ValueError:
        raise BadRequest("Delay must be a number of seconds.") from None
    if requested < 0:
        raise BadRequest("Delay must not be negative.")

    capped = min(requested, current_app.config["MAX_DELAY_SECONDS"])
    started = time.perf_counter()
    time.sleep(capped)
    return jsonify(
        {
            "requested_seconds": requested,
            "slept_seconds": round(time.perf_counter() - started, 3),
            "max_seconds": current_app.config["MAX_DELAY_SECONDS"],
            "request_id": g.get("request_id"),
        }
    )


@bp.get("/stream")
def stream():
    """Send a chunked response slowly, to reveal proxy buffering."""
    chunks = _positive_int("chunks", 5, current_app.config["MAX_STREAM_CHUNKS"])
    pause = _positive_float("delay", 0.5, current_app.config["MAX_DELAY_SECONDS"])
    budget = current_app.config["MAX_STREAM_SECONDS"]
    request_id = g.get("request_id")

    def generate() -> Iterator[str]:
        started = time.monotonic()
        for index in range(1, chunks + 1):
            elapsed = time.monotonic() - started
            if elapsed >= budget:
                yield f"aborted after {elapsed:.3f}s stream budget\n"
                return
            yield (
                f"chunk {index}/{chunks} "
                f"elapsed={elapsed:.3f}s request_id={request_id}\n"
            )
            if index < chunks:
                time.sleep(pause)

    response = Response(stream_with_context(generate()), mimetype="text/plain")
    response.headers["Cache-Control"] = "no-store"
    if request.args.get("nobuffer", "").strip().lower() in {"1", "true", "yes"}:
        # nginx-specific opt-out; other proxies ignore it.
        response.headers["X-Accel-Buffering"] = "no"
    return response


@bp.get("/sse")
def sse():
    """Emit Server-Sent Events, the classic proxy-buffering trap."""
    events = _positive_int("events", 5, current_app.config["MAX_STREAM_CHUNKS"])
    pause = _positive_float("delay", 1.0, current_app.config["MAX_DELAY_SECONDS"])
    budget = current_app.config["MAX_STREAM_SECONDS"]

    def generate() -> Iterator[str]:
        started = time.monotonic()
        for index in range(1, events + 1):
            elapsed = time.monotonic() - started
            if elapsed >= budget:
                yield "event: aborted\ndata: stream budget exhausted\n\n"
                return
            payload = json.dumps({"index": index, "elapsed": round(elapsed, 3)})
            yield f"id: {index}\nevent: tick\ndata: {payload}\n\n"
            if index < events:
                time.sleep(pause)
        yield "event: done\ndata: {}\n\n"

    response = Response(
        stream_with_context(generate()), mimetype="text/event-stream"
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@bp.get("/bytes/<int:size>")
def bytes_payload(size: int):
    """Return a payload of the requested size, for buffer/limit testing."""
    maximum = current_app.config["MAX_PAYLOAD_BYTES"]
    if size > maximum:
        raise BadRequest(f"Requested size exceeds MAX_PAYLOAD_BYTES ({maximum}).")
    body = (b"0123456789abcdef" * (size // 16 + 1))[:size]
    response = Response(body, mimetype="application/octet-stream")
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/redirect")
def redirect_relative():
    """Redirect to a relative path (default /api/echo)."""
    target = request.args.get("to", "/api/echo")
    # Only same-origin targets: anything else would be an open redirect.
    if not target.startswith("/") or target.startswith("//"):
        raise BadRequest("Parameter 'to' must be a relative path starting with '/'.")
    raw_code = request.args.get("code", "302")
    try:
        code = int(raw_code)
    except ValueError:
        raise BadRequest("Parameter 'code' must be an integer.") from None
    if code not in {301, 302, 303, 307, 308}:
        raise BadRequest("Parameter 'code' must be one of 301, 302, 303, 307, 308.")
    return redirect(target, code=code)


@bp.get("/redirect/absolute")
def redirect_absolute():
    """Redirect to an absolute URL built from scheme/host as seen here."""
    location = url_for("api.echo", _external=True)
    return redirect(location, code=302)


@bp.get("/cookies")
def cookies():
    """Show the cookies that arrived at the backend."""
    return jsonify({"cookies": dict(request.cookies)})


@bp.get("/cookies/set")
def cookies_set():
    """Set a cookie, to check proxy cookie/domain rewriting."""
    name = request.args.get("name", "proxytest")
    value = request.args.get("value", "1")
    if not name.isascii() or not name.strip() or any(c in name for c in ' ;,="\\'):
        raise BadRequest("Cookie name contains invalid characters.")
    if not value.isascii() or any(c in value for c in ' ;,="\\'):
        raise BadRequest("Cookie value contains invalid characters.")

    samesite = request.args.get("samesite", "Lax").capitalize()
    if samesite not in COOKIE_SAMESITE_VALUES:
        raise BadRequest("Parameter 'samesite' must be Strict, Lax or None.")
    max_age = _positive_int("max_age", 3600, 86400)

    response = jsonify(
        {
            "set": {
                "name": name,
                "value": value,
                "max_age": max_age,
                "samesite": samesite,
                "secure": request.is_secure,
            }
        }
    )
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        # Follows the scheme the backend sees, so it depends on
        # X-Forwarded-Proto being forwarded correctly.
        secure=request.is_secure,
        samesite=samesite,
        path="/",
    )
    return response
