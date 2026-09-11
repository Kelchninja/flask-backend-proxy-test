"""Gunicorn configuration.

Run with:  gunicorn --config gunicorn.conf.py flaskbackendtest.wsgi:app
Every value can be overridden through environment variables.
"""

from __future__ import annotations

import multiprocessing
import os


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


# --- socket -----------------------------------------------------------------
bind = os.environ.get("BIND", "0.0.0.0:8000")
backlog = _int_env("BACKLOG", 2048)

# --- worker processes --------------------------------------------------------
# gthread keeps memory low while still handling the slow/streaming endpoints
# of this service without blocking a whole process per request.
worker_class = os.environ.get("WORKER_CLASS", "gthread")
workers = _int_env("WEB_CONCURRENCY", multiprocessing.cpu_count() * 2 + 1)
threads = _int_env("THREADS", 4)
worker_tmp_dir = os.environ.get("WORKER_TMP_DIR") or None

# Must stay larger than MAX_STREAM_SECONDS/MAX_DELAY_SECONDS, otherwise
# gunicorn kills workers that are legitimately streaming a slow response.
timeout = _int_env("TIMEOUT", 150)
graceful_timeout = _int_env("GRACEFUL_TIMEOUT", 30)
# Should be higher than the reverse proxy's keepalive timeout so the proxy,
# not the backend, closes idle connections.
keepalive = _int_env("KEEPALIVE", 15)

# Recycle workers to bound the impact of any slow memory growth.
max_requests = _int_env("MAX_REQUESTS", 1000)
max_requests_jitter = _int_env("MAX_REQUESTS_JITTER", 100)

# Fork after import: less memory, faster boot, config errors fail immediately.
preload_app = os.environ.get("PRELOAD_APP", "true").lower() not in {"0", "false", "no"}

# --- security ----------------------------------------------------------------
# Only these peers may set X-Forwarded-*; set to your proxy's IP, or "*" when
# the container is only reachable through the proxy network.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
limit_request_line = _int_env("LIMIT_REQUEST_LINE", 8190)
limit_request_fields = _int_env("LIMIT_REQUEST_FIELDS", 100)
limit_request_field_size = _int_env("LIMIT_REQUEST_FIELD_SIZE", 8190)

# --- logging -----------------------------------------------------------------
# The application writes its own structured access log; gunicorn only reports
# its lifecycle and errors, on stdout/stderr.
accesslog = None
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
