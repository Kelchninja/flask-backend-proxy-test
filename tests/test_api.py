from __future__ import annotations

import json


def test_echo_reflects_request(client):
    response = client.post(
        "/api/echo?foo=1&foo=2&bar=x",
        json={"hello": "world"},
        headers={"X-Custom": "value"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["method"] == "POST"
    assert payload["path"] == "/api/echo"
    assert payload["query_params"] == {"foo": ["1", "2"], "bar": "x"}
    assert payload["headers"]["X-Custom"] == "value"
    assert payload["body"]["json"] == {"hello": "world"}
    assert payload["body"]["encoding"] == "utf-8"
    assert payload["request_id"]


def test_echo_handles_form_data(client):
    response = client.post("/api/echo", data={"a": "1", "b": "2"})
    body = response.get_json()["body"]
    assert body["encoding"] == "form"
    assert body["form"] == {"a": "1", "b": "2"}


def test_echo_handles_binary_body(client):
    response = client.post(
        "/api/echo", data=b"\xff\xfe\x00", content_type="application/octet-stream"
    )
    body = response.get_json()["body"]
    assert body["encoding"] == "base64"
    assert body["size_bytes"] == 3


def test_echo_truncates_large_bodies(app_factory):
    app = app_factory({"MAX_ECHO_BODY_BYTES": 10})
    response = app.test_client().post(
        "/api/echo", data=b"a" * 100, content_type="text/plain"
    )
    body = response.get_json()["body"]
    assert body["truncated"] is True
    assert body["data"] == "a" * 10


def test_body_larger_than_limit_is_rejected(app_factory):
    app = app_factory({"MAX_CONTENT_LENGTH": 32})
    response = app.test_client().post(
        "/api/echo", data=b"x" * 1024, content_type="text/plain"
    )
    assert response.status_code == 413
    assert response.get_json()["status"] == 413


def test_headers_endpoint(client):
    payload = client.get("/api/headers", headers={"X-Test": "1"}).get_json()
    assert payload["headers"]["X-Test"] == "1"
    assert payload["count"] == len(payload["headers"])


def test_status_endpoint(client):
    assert client.get("/api/status/503").status_code == 503
    assert client.get("/api/status/503").get_json()["requested_status"] == 503

    empty = client.get("/api/status/204")
    assert empty.status_code == 204
    assert empty.data == b""

    bad = client.get("/api/status/999")
    assert bad.status_code == 400


def test_delay_is_capped(app_factory):
    app = app_factory({"MAX_DELAY_SECONDS": 0.05})
    payload = app.test_client().get("/api/delay/10").get_json()
    assert payload["requested_seconds"] == 10
    assert payload["slept_seconds"] < 1

    assert app.test_client().get("/api/delay/abc").status_code == 400


def test_stream_yields_all_chunks(client):
    response = client.get("/api/stream?chunks=3&delay=0")
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    lines = response.get_data(as_text=True).strip().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("chunk 1/3")


def test_stream_nobuffer_hint(client):
    response = client.get("/api/stream?chunks=1&delay=0&nobuffer=1")
    assert response.headers["X-Accel-Buffering"] == "no"


def test_stream_rejects_invalid_params(client):
    assert client.get("/api/stream?chunks=abc").status_code == 400
    assert client.get("/api/stream?chunks=-1").status_code == 400


def test_sse_stream(client):
    response = client.get("/api/sse?events=2&delay=0")
    assert response.mimetype == "text/event-stream"
    body = response.get_data(as_text=True)
    assert "event: tick" in body
    assert "event: done" in body
    first = body.split("\n\n")[0]
    data_line = [line for line in first.splitlines() if line.startswith("data: ")][0]
    assert json.loads(data_line.removeprefix("data: "))["index"] == 1


def test_bytes_payload(client):
    response = client.get("/api/bytes/1024")
    assert response.status_code == 200
    assert len(response.data) == 1024
    assert response.headers["Content-Length"] == "1024"


def test_bytes_payload_respects_limit(app_factory):
    app = app_factory({"MAX_PAYLOAD_BYTES": 16})
    assert app.test_client().get("/api/bytes/17").status_code == 400
    assert len(app.test_client().get("/api/bytes/16").data) == 16


def test_relative_redirect(client):
    response = client.get("/api/redirect?to=/api/whoami&code=307")
    assert response.status_code == 307
    assert response.headers["Location"] == "/api/whoami"


def test_open_redirect_is_blocked(client):
    assert client.get("/api/redirect?to=https://evil.example").status_code == 400
    assert client.get("/api/redirect?to=//evil.example").status_code == 400
    assert client.get("/api/redirect?to=/x&code=418").status_code == 400


def test_cookies_roundtrip(client):
    response = client.get("/api/cookies/set?name=proxytest&value=abc")
    assert response.status_code == 200
    cookie = response.headers["Set-Cookie"]
    assert "proxytest=abc" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie

    echoed = client.get("/api/cookies").get_json()
    assert echoed["cookies"]["proxytest"] == "abc"


def test_cookie_name_is_validated(client):
    assert client.get("/api/cookies/set?name=bad;name").status_code == 400
    assert client.get("/api/cookies/set?samesite=nope").status_code == 400
