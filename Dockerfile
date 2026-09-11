# syntax=docker/dockerfile:1

# ---------- build stage -------------------------------------------------------
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first: this layer is cached until the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-editable

COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# ---------- runtime stage -----------------------------------------------------
FROM python:3.14-slim

RUN useradd --system --create-home --uid 10001 app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    BIND=0.0.0.0:8000

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app gunicorn.conf.py ./

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

CMD ["gunicorn", "--config", "gunicorn.conf.py", "flaskbackendtest.wsgi:app"]
