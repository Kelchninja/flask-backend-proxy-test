"""Tests for the reverse-proxy specific behaviour (ProxyFix, forwarding)."""

from __future__ import annotations

import pytest

PROXY_HEADERS = {
    "X-Forwarded-For": "203.0.113.7",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": "proxy.example.com",
}


def test_client_is_taken_from_forwarded_headers(client):
    payload = client.get("/api/whoami", headers=PROXY_HEADERS).get_json()
    assert payload["client"]["remote_addr"] == "203.0.113.7"
    assert payload["client"]["scheme"] == "https"
    assert payload["client"]["is_secure"] is True
    assert payload["client"]["host"] == "proxy.example.com"
    assert payload["client"]["behind_proxy"] is True


def test_direct_request_is_not_marked_as_proxied(client):
    payload = client.get("/api/whoami").get_json()
    assert payload["client"]["behind_proxy"] is False
    assert payload["client"]["scheme"] == "http"
    assert payload["client"]["remote_addr"] == "127.0.0.1"


def test_only_trusted_hops_are_honoured(client):
    """With one trusted hop the closest proxy entry wins, not a spoofed one."""
    payload = client.get(
        "/api/whoami",
        headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.7"},
    ).get_json()
    assert payload["client"]["remote_addr"] == "203.0.113.7"
    assert payload["client"]["forwarded_for"] == ["1.2.3.4", "203.0.113.7"]


def test_two_trusted_hops(app_factory):
    app = app_factory({"PROXY_FIX_X_FOR": 2})
    payload = (
        app.test_client()
        .get("/api/whoami", headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.7"})
        .get_json()
    )
    assert payload["client"]["remote_addr"] == "1.2.3.4"


def test_proxy_fix_can_be_disabled(app_factory):
    app = app_factory(
        {
            "PROXY_FIX_X_FOR": 0,
            "PROXY_FIX_X_PROTO": 0,
            "PROXY_FIX_X_HOST": 0,
            "PROXY_FIX_X_PORT": 0,
            "PROXY_FIX_X_PREFIX": 0,
        }
    )
    payload = app.test_client().get("/api/whoami", headers=PROXY_HEADERS).get_json()
    assert payload["client"]["remote_addr"] == "127.0.0.1"
    assert payload["client"]["scheme"] == "http"


def test_absolute_redirect_uses_forwarded_scheme_and_host(client):
    response = client.get("/api/redirect/absolute", headers=PROXY_HEADERS)
    assert response.status_code == 302
    assert response.headers["Location"] == "https://proxy.example.com/api/echo"


def test_external_url_in_index_follows_proxy(client):
    payload = client.get("/", headers=PROXY_HEADERS).get_json()
    assert payload["self_url"] == "https://proxy.example.com/"


def test_prefix_stripping_proxy(app_factory):
    """A proxy that strips /backend must send X-Forwarded-Prefix."""
    app = app_factory({"PROXY_FIX_X_PREFIX": 1})
    payload = (
        app.test_client()
        .get(
            "/api/echo",
            headers={**PROXY_HEADERS, "X-Forwarded-Prefix": "/backend"},
        )
        .get_json()
    )
    assert payload["client"]["script_root"] == "/backend"
    assert payload["url"] == "https://proxy.example.com/backend/api/echo"


def test_cookie_secure_flag_follows_forwarded_proto(client):
    response = client.get("/api/cookies/set?name=t&value=1", headers=PROXY_HEADERS)
    assert "Secure" in response.headers["Set-Cookie"]

    plain = client.get("/api/cookies/set?name=t&value=1")
    assert "Secure" not in plain.headers["Set-Cookie"]


@pytest.mark.parametrize("host", ["proxy.example.com", "other.example.com"])
def test_trusted_hosts_allow_list(app_factory, host):
    app = app_factory({"TRUSTED_HOSTS": ["proxy.example.com"]})
    response = app.test_client().get(
        "/healthz", headers={"X-Forwarded-Host": host}
    )
    expected = 200 if host == "proxy.example.com" else 400
    assert response.status_code == expected
