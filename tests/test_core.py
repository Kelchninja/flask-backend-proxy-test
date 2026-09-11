from __future__ import annotations


def test_index_lists_endpoints(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    payload = response.get_json()
    assert payload["environment"] == "testing"
    rules = {entry["rule"] for entry in payload["docs"]["endpoints"]}
    assert {"/", "/healthz", "/readyz", "/api/echo"} <= rules


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_readyz(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"


def test_version(client):
    payload = client.get("/version").get_json()
    assert payload["name"]
    assert payload["instance"] == "test-instance"
    assert payload["pid"] > 0


def test_security_and_tracing_headers(client):
    response = client.get("/healthz")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Served-By"] == "test-instance"
    assert float(response.headers["X-Response-Time-Ms"]) >= 0
    assert response.headers["X-Request-ID"]


def test_incoming_request_id_is_reused(client):
    response = client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"


def test_invalid_request_id_is_replaced(client):
    response = client.get("/healthz", headers={"X-Request-ID": "bad id\twith space"})
    assert response.headers["X-Request-ID"] != "bad id\twith space"


def test_unknown_path_returns_json_error(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    payload = response.get_json()
    assert payload["status"] == 404
    assert payload["error"] == "not_found"
    assert payload["request_id"]


def test_method_not_allowed_keeps_allow_header(client):
    response = client.post("/healthz")
    assert response.status_code == 405
    assert "GET" in response.headers["Allow"]
    assert response.get_json()["error"] == "method_not_allowed"
