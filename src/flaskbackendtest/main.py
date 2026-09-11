"""Development entry point.

Starts Werkzeug's built-in server, which is single-threaded-ish and not meant
for production traffic. Use gunicorn (see gunicorn.conf.py) for real runs.
"""

from __future__ import annotations

import os

from flaskbackendtest import create_app

app = create_app()


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
