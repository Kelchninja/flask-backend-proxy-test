#!/usr/bin/env python3
"""Testet einen Reverse Proxy gegen das Flask Proxy Test Backend.

Nur Standardbibliothek, keine Abhängigkeiten. Aufruf:

    python3 proxy_test.py http://localhost:8080
    python3 proxy_test.py https://proxy.example.com --insecure --delay 20

Der Proxy muss auf ein laufendes Backend zeigen (flaskbackendtest). Jeder
Check meldet PASS, FAIL, WARN oder INFO; der Exit-Code ist 1, sobald ein
Check FAIL meldet.
"""

from __future__ import annotations

import argparse
import http.client
import json
import ssl
import sys
import time
import urllib.parse
import uuid
from collections.abc import Callable
from dataclasses import dataclass

PASS, FAIL, WARN, INFO = "PASS", "FAIL", "WARN", "INFO"


# --------------------------------------------------------------------------
# HTTP-Hilfen
# --------------------------------------------------------------------------
@dataclass
class Response:
    status: int
    headers: list[tuple[str, str]]
    body: bytes
    version: int  # 10 oder 11

    def header(self, name: str) -> str | None:
        name = name.lower()
        for key, value in self.headers:
            if key.lower() == name:
                return value
        return None

    def header_all(self, name: str) -> list[str]:
        name = name.lower()
        return [value for key, value in self.headers if key.lower() == name]

    def json(self) -> dict:
        try:
            return json.loads(self.body)
        except ValueError as exc:
            snippet = self.body[:120].decode("utf-8", "replace")
            raise AssertionError(
                f"Antwort ist kein JSON (Status {self.status}): {snippet!r}"
            ) from exc


class Client:
    def __init__(self, base_url: str, timeout: float, insecure: bool) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SystemExit(f"Ungültige URL: {base_url!r} (erwartet http(s)://host[:port][/prefix])")
        self.scheme = parsed.scheme
        self.netloc = parsed.netloc
        self.prefix = parsed.path.rstrip("/")
        self.base = f"{self.scheme}://{self.netloc}{self.prefix}"
        self.timeout = timeout
        self.insecure = insecure

    @property
    def expected_host(self) -> str:
        """Host so, wie das Backend ihn sehen sollte (ohne Default-Port)."""
        host, _, port = self.netloc.rpartition(":")
        default = "443" if self.scheme == "https" else "80"
        if host and port == default:
            return host
        return self.netloc

    def connect(self, timeout: float | None = None) -> http.client.HTTPConnection:
        timeout = self.timeout if timeout is None else timeout
        if self.scheme == "https":
            context = ssl.create_default_context()
            if self.insecure:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            return http.client.HTTPSConnection(self.netloc, timeout=timeout, context=context)
        return http.client.HTTPConnection(self.netloc, timeout=timeout)

    def request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: float | None = None,
    ) -> Response:
        conn = self.connect(timeout)
        try:
            conn.request(method, self.prefix + path, body=body, headers=headers or {})
            raw = conn.getresponse()
            return Response(raw.status, raw.getheaders(), raw.read(), raw.version)
        finally:
            conn.close()

    def stream(
        self, path: str, timeout: float | None = None
    ) -> tuple[Response, list[tuple[float, bytes]]]:
        """Liest eine Antwort zeilenweise und merkt sich die Ankunftszeit."""
        conn = self.connect(timeout)
        started = time.monotonic()
        lines: list[tuple[float, bytes]] = []
        try:
            conn.request("GET", self.prefix + path)
            raw = conn.getresponse()
            while True:
                line = raw.readline()
                if not line:
                    break
                lines.append((time.monotonic() - started, line))
            body = b"".join(line for _, line in lines)
            return Response(raw.status, raw.getheaders(), body, raw.version), lines
        finally:
            conn.close()


# --------------------------------------------------------------------------
# Ergebnisse
# --------------------------------------------------------------------------
@dataclass
class Result:
    name: str
    status: str
    detail: str = ""


class Reporter:
    COLORS = {PASS: "32", FAIL: "31", WARN: "33", INFO: "36"}
    SYMBOLS = {PASS: "✔", FAIL: "✘", WARN: "!", INFO: "i"}

    def __init__(self, color: bool) -> None:
        self.color = color
        self.results: list[Result] = []

    def add(self, result: Result) -> None:
        self.results.append(result)
        symbol = self.SYMBOLS[result.status]
        label = f"{symbol} {result.status}"
        if self.color:
            label = f"\033[{self.COLORS[result.status]}m{label}\033[0m"
        line = f"  {label:<{16 if self.color else 7}} {result.name}"
        if result.detail:
            line += f"\n{'':10}{result.detail}"
        print(line, flush=True)

    def section(self, title: str) -> None:
        print(f"\n{title}", flush=True)

    def summary(self) -> int:
        counts = {status: 0 for status in (PASS, FAIL, WARN, INFO)}
        for result in self.results:
            counts[result.status] += 1
        print(
            f"\n{counts[PASS]} PASS, {counts[FAIL]} FAIL, {counts[WARN]} WARN, "
            f"{counts[INFO]} INFO"
        )
        if counts[FAIL]:
            print("Fehlgeschlagen:")
            for result in self.results:
                if result.status == FAIL:
                    print(f"  - {result.name}")
        return 1 if counts[FAIL] else 0


Check = Callable[[Client, argparse.Namespace], list[Result]]


def run_check(reporter: Reporter, client: Client, args: argparse.Namespace, check: Check) -> None:
    try:
        for result in check(client, args):
            reporter.add(result)
    except (OSError, http.client.HTTPException) as exc:
        reporter.add(Result(check.__name__, FAIL, f"Verbindungsfehler: {exc}"))
    except AssertionError as exc:
        reporter.add(Result(check.__name__, FAIL, str(exc)))


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------
def check_backend_reachable(client: Client, args: argparse.Namespace) -> list[Result]:
    response = client.request("GET", "/")
    if response.status != 200:
        raise AssertionError(f"GET / lieferte {response.status} statt 200")
    payload = response.json()
    if "docs" not in payload or "endpoints" not in payload["docs"]:
        raise AssertionError("Antwort sieht nicht nach dem Flask Proxy Test Backend aus")
    return [
        Result(
            "Backend über den Proxy erreichbar",
            PASS,
            f"{payload.get('name')} {payload.get('version')} auf "
            f"{payload.get('instance')} ({payload.get('environment')})",
        )
    ]


def check_forwarded_headers(client: Client, args: argparse.Namespace) -> list[Result]:
    view = client.request("GET", "/api/whoami").json()["client"]
    results = []

    if view["forwarded_for"]:
        results.append(
            Result("X-Forwarded-For wird gesetzt", PASS,
                   f"Backend sieht Client {view['remote_addr']} "
                   f"(Kette: {', '.join(view['forwarded_for'])})")
        )
    else:
        results.append(
            Result("X-Forwarded-For wird gesetzt", FAIL,
                   f"Header fehlt, Backend hält {view['remote_addr']} (den Proxy) für den Client")
        )

    proto = view["forwarded_proto"]
    if proto is None:
        results.append(
            Result("X-Forwarded-Proto wird gesetzt", FAIL,
                   "Header fehlt; hinter TLS würde das Backend http annehmen "
                   "(falsche Redirects, Cookies ohne Secure)")
        )
    elif proto.lower() != client.scheme:
        results.append(
            Result("X-Forwarded-Proto wird gesetzt", FAIL,
                   f"Header sagt {proto!r}, der Client kam aber per {client.scheme}")
        )
    else:
        results.append(Result("X-Forwarded-Proto wird gesetzt", PASS, f"scheme={view['scheme']}"))

    if view["host"] == client.expected_host:
        results.append(Result("Host kommt unverändert an", PASS, f"host={view['host']}"))
    else:
        results.append(
            Result("Host kommt unverändert an", FAIL,
                   f"Backend sieht Host {view['host']!r}, erwartet {client.expected_host!r}. "
                   "Proxy muss Host durchreichen oder X-Forwarded-Host setzen.")
        )

    results.append(
        Result("HTTP-Version Proxy → Backend", PASS if view["http_version"] == "HTTP/1.1" else WARN,
               f"{view['http_version']}"
               + ("" if view["http_version"] == "HTTP/1.1"
                  else " – ohne HTTP/1.1 kein Keep-Alive und kein Chunked-Streaming zum Backend"))
    )

    optional = {
        "X-Forwarded-Port": view["forwarded_port"],
        "Forwarded (RFC 7239)": view["forwarded"],
        "Via": view["via"],
    }
    for name, value in optional.items():
        results.append(
            Result(f"{name} (optional)", INFO, f"{value!r}" if value else "nicht gesetzt")
        )
    return results


def check_spoofed_client_ip(client: Client, args: argparse.Namespace) -> list[Result]:
    fake = "203.0.113.99"
    view = client.request("GET", "/api/whoami", {"X-Forwarded-For": fake}).json()["client"]
    chain = view["forwarded_for"]
    if view["remote_addr"] == fake:
        return [
            Result("Gefälschtes X-Forwarded-For wird neutralisiert", FAIL,
                   f"Backend übernimmt {fake} als Client-IP. Der Proxy reicht den Header "
                   "ungeprüft durch – Clients können ihre IP fälschen.")
        ]
    mode = "hängt eigene Adresse an" if fake in chain else "ersetzt den Header"
    return [
        Result("Gefälschtes X-Forwarded-For wird neutralisiert", PASS,
               f"Proxy {mode}; Backend sieht {view['remote_addr']} (Kette: {', '.join(chain)})")
    ]


def check_request_headers(client: Client, args: argparse.Namespace) -> list[Result]:
    marker = uuid.uuid4().hex
    echoed = client.request("GET", "/api/echo", {"X-Proxy-Test": marker}).json()["headers"]
    ok = echoed.get("X-Proxy-Test") == marker
    return [
        Result("Eigene Request-Header kommen durch", PASS if ok else FAIL,
               "" if ok else f"X-Proxy-Test fehlt im Backend (angekommen: {sorted(echoed)})")
    ]


def check_request_id(client: Client, args: argparse.Namespace) -> list[Result]:
    marker = f"proxytest-{uuid.uuid4().hex[:12]}"
    response = client.request("GET", "/healthz", {"X-Request-ID": marker})
    got = response.header("X-Request-ID")
    if got == marker:
        return [Result("X-Request-ID wird in beide Richtungen durchgereicht", PASS)]
    if got is None:
        return [Result("X-Request-ID wird in beide Richtungen durchgereicht", WARN,
                       "Response-Header fehlt – der Proxy entfernt ihn auf dem Rückweg")]
    return [Result("X-Request-ID wird in beide Richtungen durchgereicht", WARN,
                   f"Gesendet {marker!r}, zurück kam {got!r} – der Proxy überschreibt die ID")]


def check_response_headers(client: Client, args: argparse.Namespace) -> list[Result]:
    response = client.request("GET", "/healthz")
    missing = [name for name in ("X-Served-By", "X-Response-Time-Ms", "X-Content-Type-Options")
               if response.header(name) is None]
    if missing:
        return [Result("Backend-Response-Header kommen durch", FAIL,
                       f"Vom Proxy entfernt: {', '.join(missing)}")]
    results = [Result("Backend-Response-Header kommen durch", PASS,
                      f"served-by={response.header('X-Served-By')}")]
    server = response.header("Server")
    results.append(Result("Server-Header (optional)", INFO,
                          f"{server!r}" if server else "nicht gesetzt"))
    return results


def check_methods(client: Client, args: argparse.Namespace) -> list[Result]:
    wrong = []
    for method in ("PUT", "PATCH", "DELETE", "OPTIONS"):
        response = client.request(method, "/api/echo")
        seen = response.json().get("method") if response.status == 200 else None
        if seen != method:
            wrong.append(f"{method}→{response.status}/{seen}")
    if wrong:
        return [Result("HTTP-Methoden werden durchgereicht", FAIL, ", ".join(wrong))]
    return [Result("HTTP-Methoden werden durchgereicht", PASS, "PUT, PATCH, DELETE, OPTIONS")]


def check_head(client: Client, args: argparse.Namespace) -> list[Result]:
    response = client.request("HEAD", "/healthz")
    if response.status != 200:
        return [Result("HEAD-Requests", FAIL, f"Status {response.status}")]
    if response.body:
        return [Result("HEAD-Requests", FAIL, f"HEAD-Antwort enthält {len(response.body)} Bytes Body")]
    return [Result("HEAD-Requests", PASS)]


def check_query_string(client: Client, args: argparse.Namespace) -> list[Result]:
    query = "a=1&b=x%20y&c=%C3%A4&d=1&d=2&e=a%2Bb%26c"
    expected = {"a": "1", "b": "x y", "c": "ä", "d": ["1", "2"], "e": "a+b&c"}
    got = client.request("GET", f"/api/echo?{query}").json()["query_params"]
    if got == expected:
        return [Result("Query-String bleibt intakt (Encoding, Mehrfachwerte)", PASS)]
    return [Result("Query-String bleibt intakt (Encoding, Mehrfachwerte)", FAIL,
                   f"erwartet {expected}, bekommen {got}")]


def check_json_body(client: Client, args: argparse.Namespace) -> list[Result]:
    payload = {"proxy": "test", "n": 42, "unicode": "äöü€", "nested": {"ok": True}}
    body = json.dumps(payload).encode("utf-8")
    echoed = client.request(
        "POST", "/api/echo", {"Content-Type": "application/json"}, body
    ).json()["body"]
    if echoed.get("json") == payload:
        return [Result("JSON-Body kommt unverändert an", PASS)]
    return [Result("JSON-Body kommt unverändert an", FAIL, f"Backend sah: {echoed}")]


def check_large_upload(client: Client, args: argparse.Namespace) -> list[Result]:
    size = args.upload_mb * 1024 * 1024
    body = b"u" * size
    response = client.request(
        "POST", "/api/echo", {"Content-Type": "application/octet-stream"}, body
    )
    name = f"Upload von {args.upload_mb} MiB"
    if response.status == 413:
        return [Result(name, WARN, "413 – Proxy oder Backend (MAX_CONTENT_LENGTH) begrenzt die Body-Größe")]
    if response.status != 200:
        return [Result(name, FAIL, f"Status {response.status}")]
    seen = response.json()["body"].get("size_bytes")
    if seen == size:
        return [Result(name, PASS, f"{size} Bytes vollständig angekommen")]
    return [Result(name, FAIL, f"Backend sah {seen} von {size} Bytes")]


def check_large_download(client: Client, args: argparse.Namespace) -> list[Result]:
    size = args.download_mb * 1024 * 1024
    response = client.request("GET", f"/api/bytes/{size}")
    name = f"Download von {args.download_mb} MiB"
    if response.status != 200:
        return [Result(name, FAIL, f"Status {response.status}: {response.body[:100]!r}")]
    if len(response.body) != size:
        return [Result(name, FAIL, f"{len(response.body)} von {size} Bytes empfangen")]
    return [Result(name, PASS, f"Content-Length={response.header('Content-Length')}")]


def check_status_codes(client: Client, args: argparse.Namespace) -> list[Result]:
    problems = []
    for code in (201, 204, 404, 418, 429, 500, 502, 503):
        response = client.request("GET", f"/api/status/{code}")
        if response.status != code:
            problems.append(f"{code}→{response.status}")
            continue
        if code == 204:
            if response.body:
                problems.append("204 mit Body")
            continue
        try:
            if response.json().get("requested_status") != code:
                problems.append(f"{code}: fremder Body")
        except AssertionError:
            problems.append(f"{code}: Proxy ersetzt die Fehlerseite")
    if problems:
        return [Result("Statuscodes werden unverändert durchgereicht", FAIL, ", ".join(problems))]
    return [Result("Statuscodes werden unverändert durchgereicht", PASS,
                   "201, 204, 404, 418, 429, 500, 502, 503")]


def check_relative_redirect(client: Client, args: argparse.Namespace) -> list[Result]:
    response = client.request("GET", "/api/redirect?to=/api/whoami&code=307")
    location = response.header("Location") or ""
    if response.status != 307:
        return [Result("Relativer Redirect", FAIL, f"Status {response.status} statt 307")]
    accepted = {"/api/whoami", f"{client.prefix}/api/whoami", f"{client.base}/api/whoami"}
    if location in accepted:
        return [Result("Relativer Redirect", PASS, f"Location: {location}")]
    return [Result("Relativer Redirect", FAIL, f"Location: {location!r}")]


def check_absolute_redirect(client: Client, args: argparse.Namespace) -> list[Result]:
    response = client.request("GET", "/api/redirect/absolute")
    location = response.header("Location") or ""
    expected = f"{client.base}/api/echo"
    if response.status != 302:
        return [Result("Absoluter Redirect zeigt auf den Proxy", FAIL, f"Status {response.status}")]
    if location == expected:
        return [Result("Absoluter Redirect zeigt auf den Proxy", PASS, f"Location: {location}")]
    return [Result("Absoluter Redirect zeigt auf den Proxy", FAIL,
                   f"Location: {location!r}, erwartet {expected!r} – "
                   "X-Forwarded-Proto/-Host fehlen oder stimmen nicht")]


def check_cookies(client: Client, args: argparse.Namespace) -> list[Result]:
    value = uuid.uuid4().hex[:8]
    response = client.request("GET", f"/api/cookies/set?name=proxytest&value={value}")
    set_cookie = "; ".join(response.header_all("Set-Cookie"))
    results = []
    if f"proxytest={value}" in set_cookie:
        results.append(Result("Set-Cookie kommt beim Client an", PASS, set_cookie))
    else:
        results.append(Result("Set-Cookie kommt beim Client an", FAIL,
                              f"Set-Cookie: {set_cookie!r}"))
    if client.scheme == "https" and "Secure" not in set_cookie:
        results.append(Result("Cookie hat Secure-Flag hinter TLS", FAIL,
                              "Backend hält die Verbindung für http – X-Forwarded-Proto prüfen"))

    echoed = client.request("GET", "/api/cookies", {"Cookie": f"proxytest={value}"}).json()
    if echoed["cookies"].get("proxytest") == value:
        results.append(Result("Cookie-Header kommt beim Backend an", PASS))
    else:
        results.append(Result("Cookie-Header kommt beim Backend an", FAIL,
                              f"Backend sah {echoed['cookies']}"))
    return results


def _streaming_verdict(name: str, lines: list[tuple[float, bytes]], count: int,
                       pause: float, response: Response) -> Result:
    if response.status != 200:
        return Result(name, FAIL, f"Status {response.status}")
    data_lines = [t for t, line in lines if line.strip()]
    if len(data_lines) < count:
        return Result(name, FAIL, f"nur {len(data_lines)} von {count} Chunks empfangen")
    expected = (count - 1) * pause
    spread = data_lines[-1] - data_lines[0]
    first = data_lines[0]
    if spread >= expected * 0.5:
        return Result(name, PASS,
                      f"erster Chunk nach {first:.2f}s, Chunks über {spread:.2f}s verteilt – "
                      "Proxy streamt")
    return Result(name, WARN,
                  f"alle {count} Chunks innerhalb von {spread:.2f}s nach {first:.2f}s – "
                  "Proxy puffert die Antwort (SSE/Streaming funktioniert so nicht)")


def check_streaming(client: Client, args: argparse.Namespace) -> list[Result]:
    count, pause = 5, 0.4
    response, lines = client.stream(f"/api/stream?chunks={count}&delay={pause}")
    return [_streaming_verdict("Chunked-Streaming (/api/stream)", lines, count, pause, response)]


def check_sse(client: Client, args: argparse.Namespace) -> list[Result]:
    count, pause = 4, 0.4
    response, lines = client.stream(f"/api/sse?events={count}&delay={pause}")
    results = []
    ctype = response.header("Content-Type") or ""
    if not ctype.startswith("text/event-stream"):
        results.append(Result("SSE Content-Type bleibt erhalten", FAIL, f"Content-Type: {ctype!r}"))
    else:
        results.append(Result("SSE Content-Type bleibt erhalten", PASS))
    events = [(t, line) for t, line in lines if line.startswith(b"event: tick")]
    results.append(_streaming_verdict("Server-Sent Events (/api/sse)", events, count, pause, response))
    return results


def check_slow_response(client: Client, args: argparse.Namespace) -> list[Result]:
    seconds = args.delay
    name = f"Langsame Antwort ({seconds:g}s) kommt durch"
    started = time.monotonic()
    try:
        response = client.request("GET", f"/api/delay/{seconds}", timeout=seconds + 15)
    except (OSError, http.client.HTTPException) as exc:
        elapsed = time.monotonic() - started
        return [Result(name, FAIL, f"Verbindung nach {elapsed:.1f}s abgebrochen ({exc}) – "
                                   f"Proxy-Read-Timeout liegt unter {seconds:g}s")]
    elapsed = time.monotonic() - started
    if response.status == 200:
        return [Result(name, PASS, f"200 nach {elapsed:.1f}s – Proxy wartet mindestens {seconds:g}s")]
    return [Result(name, FAIL, f"Status {response.status} nach {elapsed:.1f}s – "
                               f"Proxy-Read-Timeout liegt unter {seconds:g}s")]


def check_keepalive(client: Client, args: argparse.Namespace) -> list[Result]:
    conn = client.connect()
    try:
        first_close = None
        for _ in range(3):
            conn.request("GET", client.prefix + "/healthz")
            raw = conn.getresponse()
            raw.read()
            if first_close is None:
                first_close = (raw.getheader("Connection") or "").lower()
            if raw.status != 200:
                return [Result("Keep-Alive zum Proxy", FAIL, f"Status {raw.status}")]
    except (OSError, http.client.HTTPException) as exc:
        return [Result("Keep-Alive zum Proxy", WARN,
                       f"zweiter Request auf derselben Verbindung scheiterte: {exc}")]
    finally:
        conn.close()
    if first_close == "close":
        return [Result("Keep-Alive zum Proxy", INFO, "Proxy sendet Connection: close – "
                                                    "jede Anfrage baut eine neue Verbindung auf")]
    return [Result("Keep-Alive zum Proxy", PASS, "3 Requests über eine Verbindung")]


CHECKS: list[tuple[str, list[Check]]] = [
    ("Erreichbarkeit", [check_backend_reachable]),
    ("Forwarded-Header", [check_forwarded_headers, check_spoofed_client_ip]),
    ("Header-Durchleitung", [check_request_headers, check_request_id, check_response_headers]),
    ("Methoden und Daten", [check_methods, check_head, check_query_string, check_json_body,
                            check_status_codes, check_cookies]),
    ("Redirects", [check_relative_redirect, check_absolute_redirect]),
    ("Große Bodies", [check_large_upload, check_large_download]),
    ("Streaming und Timeouts", [check_streaming, check_sse, check_slow_response, check_keepalive]),
]


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("proxy_url", help="Basis-URL des Proxys, z.B. http://localhost:8080")
    parser.add_argument("--delay", type=float, default=10.0,
                        help="Sekunden für den Timeout-Test (Default: 10; Backend-Limit MAX_DELAY_SECONDS)")
    parser.add_argument("--upload-mb", type=int, default=2, help="Upload-Größe in MiB (Default: 2)")
    parser.add_argument("--download-mb", type=int, default=5, help="Download-Größe in MiB (Default: 5)")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="Socket-Timeout pro Request in Sekunden (Default: 30)")
    parser.add_argument("--insecure", action="store_true", help="TLS-Zertifikat nicht prüfen")
    parser.add_argument("--no-color", action="store_true", help="Ohne Farben ausgeben")
    parser.add_argument("--skip-slow", action="store_true",
                        help="Timeout-, Streaming- und Größen-Tests überspringen")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    client = Client(args.proxy_url, args.timeout, args.insecure)
    reporter = Reporter(color=sys.stdout.isatty() and not args.no_color)

    print(f"Proxy-Test gegen {client.base}")
    for title, checks in CHECKS:
        if args.skip_slow and title in {"Große Bodies", "Streaming und Timeouts"}:
            continue
        reporter.section(title)
        for check in checks:
            run_check(reporter, client, args, check)
            if check is check_backend_reachable and reporter.results[-1].status == FAIL:
                print("\nAbbruch: Backend über den Proxy nicht erreichbar.")
                return 1
    return reporter.summary()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
