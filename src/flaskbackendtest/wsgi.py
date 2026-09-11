"""WSGI entry point for production servers.

    gunicorn --config gunicorn.conf.py flaskbackendtest.wsgi:app
"""

from __future__ import annotations

from flaskbackendtest import create_app

app = create_app()
