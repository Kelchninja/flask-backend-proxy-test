# Flask Proxy Test Backend

A small Flask service whose only job is to tell you **exactly what your reverse
proxy did to a request**: which client IP and scheme arrive at the backend,
which headers survive, how the proxy handles slow responses, streaming, large
bodies, redirects, cookies and error status codes.

The app itself is set up the way a real production service would be: app
factory, environment-driven config, `ProxyFix`, structured JSON logs with
request IDs, JSON error handling, health/readiness probes, gunicorn, a
non-root container image and a test suite.

## Quick start

```bash
uv sync --group dev
uv run flaskbackendtest          # dev server on http://127.0.0.1:8000
```

Production-style run (this is what you should point your proxy at):

```bash
APP_ENV=production SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  uv run gunicorn --config gunicorn.conf.py flaskbackendtest.wsgi:app
```

Container:

```bash
docker build -t flask-proxy-test-backend .
docker run --rm -p 8000:8000 -e SECRET_KEY=change-me -e FORWARDED_ALLOW_IPS='*' flask-proxy-test-backend
```

Whole test bed (backend + nginx on `http://localhost:8080`):

```bash
docker compose up --build
```

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Service info and endpoint list |
| `GET /healthz` | Liveness probe (never logged unless `LOG_HEALTH_REQUESTS=true`) |
| `GET /readyz` | Readiness probe, `503` when a check fails |
| `GET /version` | Version, instance ID, PID, uptime |
| `ANY /api/echo` | Full request reflection: method, URL, headers, cookies, query, body |
| `GET /api/whoami` | Condensed client view: remote IP, scheme, host, forwarded chain |
| `GET /api/headers` | Only the headers that arrived |
| `GET /api/status/<code>` | Responds with any status code 200–599 |
| `GET /api/delay/<seconds>` | Delayed response (proxy read timeouts) |
| `GET /api/stream?chunks=&delay=&nobuffer=` | Chunked response (proxy buffering) |
| `GET /api/sse?events=&delay=` | Server-Sent Events |
| `GET /api/bytes/<size>` | Payload of N bytes (buffers, body limits) |
| `GET /api/redirect?to=&code=` | Relative redirect (same-origin targets only) |
| `GET /api/redirect/absolute` | Absolute redirect built from the scheme/host seen here |
| `GET /api/cookies` | Cookies that arrived |
| `GET /api/cookies/set?name=&value=&samesite=&max_age=` | Sets a cookie |

Every response carries `X-Request-ID` (reused from the incoming header when
present), `X-Response-Time-Ms` and `X-Served-By`.

## Typical checks

```bash
BASE=http://localhost:8080          # your proxy

# 1. Does the proxy forward the real client and scheme?
curl -s $BASE/api/whoami | jq
#    remote_addr must be your client IP, not the proxy's.
#    scheme must be "https" when the client used TLS.

# 2. Which headers get through / added / dropped?
curl -s $BASE/api/echo -H 'X-Debug: hello' | jq '.headers'

# 3. Are redirect Locations rewritten correctly?
curl -sI $BASE/api/redirect/absolute | grep -i location
#    Expect https://<your-domain>/api/echo, not http://<backend>:8000/...

# 4. Read timeout: where does the proxy give up?
curl -s -o /dev/null -w '%{http_code} %{time_total}\n' $BASE/api/delay/25

# 5. Buffering: do chunks arrive one by one or all at once?
curl -N $BASE/api/stream?chunks=10\&delay=1
curl -N $BASE/api/sse?events=10\&delay=1

# 6. Body size limits in both directions
curl -s -o /dev/null -w '%{http_code} %{size_download}\n' $BASE/api/bytes/10485760
head -c 20000000 /dev/zero | curl -s -o /dev/null -w '%{http_code}\n' --data-binary @- $BASE/api/echo

# 7. Are error status codes passed through untouched?
for c in 301 400 404 429 500 502 503; do
  printf '%s -> ' $c; curl -s -o /dev/null -w '%{http_code}\n' $BASE/api/status/$c
done

# 8. Request ID correlation
curl -sI $BASE/healthz -H 'X-Request-ID: trace-42' | grep -i x-request-id
```

## Configuration

All settings come from environment variables; see [.env.example](.env.example)
for the complete list. The ones that matter for proxy testing:

| Variable | Default | Meaning |
| --- | --- | --- |
| `APP_ENV` | `development` | `production` requires `SECRET_KEY` |
| `SECRET_KEY` | random | **Required** in production |
| `PROXY_FIX_X_FOR` | `1` | Number of trusted proxies for `X-Forwarded-For` |
| `PROXY_FIX_X_PROTO` | `1` | …for `X-Forwarded-Proto` |
| `PROXY_FIX_X_HOST` | `1` | …for `X-Forwarded-Host` |
| `PROXY_FIX_X_PORT` | `0` | …for `X-Forwarded-Port` |
| `PROXY_FIX_X_PREFIX` | `0` | Set to `1` if the proxy strips a path prefix |
| `TRUSTED_HOSTS` | unset | Comma-separated `Host` allow-list |
| `MAX_CONTENT_LENGTH` | `16777216` | Request bodies above this get `413` |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | gunicorn: peers allowed to set `X-Forwarded-*` |

**Trust exactly as many hops as you actually operate.** Each count tells
`ProxyFix` how many entries to skip from the right of the header. With one
proxy, `PROXY_FIX_X_FOR=1` uses the value that proxy appended; a client sending
its own `X-Forwarded-For` cannot spoof it. Setting a higher number than you have
proxies makes spoofing trivial. If the backend is reachable directly, set all
counts to `0`.

## Proxy configuration examples

- [deploy/nginx.example.conf](deploy/nginx.example.conf) — forwarded headers,
  timeouts, and `proxy_buffering off` for the streaming endpoints
- [deploy/Caddyfile.example](deploy/Caddyfile.example)

Common findings when a proxy is misconfigured:

| Symptom in `/api/whoami` or `/api/echo` | Missing on the proxy |
| --- | --- |
| `remote_addr` is the proxy's IP | `X-Forwarded-For` / `X-Real-IP` |
| `scheme` is `http` although the client used TLS | `X-Forwarded-Proto` |
| `host` is the backend address | `X-Forwarded-Host` / `Host` passthrough |
| Redirects point at the internal backend | `X-Forwarded-Proto` + `X-Forwarded-Host` |
| `/api/stream` arrives all at once | response buffering enabled (nginx: `proxy_buffering off`) |
| Paths are missing a prefix | `X-Forwarded-Prefix` + `PROXY_FIX_X_PREFIX=1` |

## Logging

One JSON object per line on stdout (`LOG_FORMAT=text` for a readable dev
format). Each request produces an access record with request ID, method, path,
status, duration, client IP and the forwarded chain. `duration_ms` for the
streaming endpoints covers the time until the response headers are sent, not
the full stream — that is inherent to streamed responses.

## Tests

```bash
uv run pytest
```

The suite covers the endpoints, the JSON error contract and the proxy
behaviour (forwarded client/scheme/host, hop counts, prefix stripping,
absolute redirects, host allow-list).

## Layout

```
src/flaskbackendtest/
├── __init__.py        # create_app() factory
├── config.py          # environment-driven configuration
├── logging_setup.py   # structured JSON logging
├── middleware.py      # ProxyFix, request IDs, access log, security headers
├── errors.py          # JSON error handlers
├── routes.py          # /, /healthz, /readyz, /version
├── api.py             # /api/* proxy test endpoints
├── main.py            # dev server entry point
└── wsgi.py            # gunicorn entry point
gunicorn.conf.py       # production server settings
Dockerfile             # multi-stage build, non-root, healthcheck
docker-compose.yml     # backend + nginx test bed
deploy/                # nginx and Caddy example configurations
tests/                 # pytest suite
```

## Notes and limits

- The service is a test target: it has no database, no authentication and no
  state. Do not expose it on the public internet.
- WSGI normalises header names, so `/api/echo` shows canonical casing
  (`X-Custom`), not the exact casing sent on the wire.
- WebSockets are out of scope — WSGI/gunicorn's sync workers cannot serve them.
- `MAX_DELAY_SECONDS`, `MAX_STREAM_SECONDS`, `MAX_STREAM_CHUNKS`,
  `MAX_PAYLOAD_BYTES` cap the test endpoints so a stray request cannot pin a
  worker forever. The gunicorn `TIMEOUT` must stay above `MAX_STREAM_SECONDS`.
