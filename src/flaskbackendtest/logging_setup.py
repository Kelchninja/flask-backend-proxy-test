"""Structured logging to stdout.

Containers should log to stdout and let the platform handle shipping and
rotation, so this module installs exactly one stream handler and formats
records as single-line JSON by default.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

HANDLER_NAME = "flaskbackendtest.stdout"

# Attributes that logging puts on every record; anything else was passed by us
# via `extra=` and belongs in the structured output.
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"asctime", "message", "taskName"}


class JsonFormatter(logging.Formatter):
    """Render log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install a single stdout handler on the root logger (idempotent)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "name", None) == HANDLER_NAME:
            root.removeHandler(handler)
            handler.close()

    handler = logging.StreamHandler(sys.stdout)
    handler.name = HANDLER_NAME
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )

    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))

    # We emit our own access log in middleware.py; keep werkzeug's dev-server
    # access log out of the way so requests are not logged twice.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
