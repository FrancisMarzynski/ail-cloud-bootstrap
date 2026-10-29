"""Scenario 7: the client's v1 transport contract, moved from the private repo.

These are the private repo's transport, proxy and framing tests, changed only for
this repo's layout and fake values. Seam: `bin/ail-memory` reading one v1 JSON request
on stdin, against a fake endpoint on 127.0.0.1 (AIL_MEMORY_ALLOW_LOOPBACK=1).
"""

import contextlib
import http.server
import json
import socket
import subprocess
import sys
import threading
import time
from typing import Any

from tests.acceptance.support import (
    AUTH,
    CLIENT,
    PAYLOAD_MARKER,
    REQUEST,
    SUCCESS,
    CloudCase,
)


class ConfirmedWrite(CloudCase):
    def test_checked_write_sql_and_parameters_are_transported_and_confirmed(self) -> None:
        # Catches a client that rewrites, drops or re-encodes SQL/parameters, or loses the JSON content type.
        # Misses: what the server does with them (the endpoint repo's tests own that).
        request = {"version": 1, "sql": "select memory.record_entry(kind => $1, body => $2)",
                   "params": ["decision", "Don't concatenate '); select private; --"]}
        confirmed = {"version": 1, "columns": ["record_entry"], "rows": [[{"id": "fixture-confirmed-id"}]]}
        endpoint = self.endpoint(confirmed)
        result = self.client(endpoint, request)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), confirmed)
        self.assertEqual(len(endpoint.calls), 1)
        self.assertEqual(json.loads(endpoint.calls[0]["body"]), request)
        self.assertEqual(endpoint.calls[0]["headers"].get("Content-Type"), "application/json")


class ResultParity(CloudCase):
    def test_representation_variants_and_duplicate_columns_preserved(self) -> None:
        # Catches lossy result handling (dict-by-column, float coercion, unicode escaping changes).
        # Misses: byte-identical formatting of stdout (only the JSON value is compared).
        values = [None, True, False, "Zażółć gęślą jaźń 🐈", {"nested": [None, {"v": 7}]},
                  [1, "x", False], "999999999999999999999999999999.000001",
                  "550e8400-e29b-41d4-a716-446655440000", "2026-10-07T11:32:00.000001Z", 42, 1.25]
        results = [{"version": 1, "columns": ["duplicate"] * len(values), "rows": [values]},
                   {"version": 1, "columns": ["empty"], "rows": []},
                   {"version": 1, "columns": [], "rows": []}]
        for response in results:
            with self.subTest(response=response):
                endpoint = self.endpoint(response)
                result = self.client(endpoint)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), response)
                self.assertEqual(json.loads(endpoint.calls[0]["body"]), REQUEST)


class Failures(CloudCase):
    def test_provider_authorization_is_explicit_and_missing_codex_auth_prevents_dispatch(self) -> None:
        # Catches a client that sends no/altered Authorization, or dispatches without a credential outside Claude.
        # Misses: whether a real provider proxy injects the header (genuine-session evidence).
        endpoint = self.endpoint()
        for provider, authorization in (("codex", AUTH), ("claude", None), ("claude", AUTH)):
            result = self.client(endpoint, provider=provider, AIL_MEMORY_AUTHORIZATION=authorization)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(endpoint.calls[-1]["headers"].get("Authorization"), authorization)
        count = len(endpoint.calls)
        extras: tuple[dict[str, Any], ...] = (
            {"provider": "codex", "AIL_MEMORY_AUTHORIZATION": None},
            {"provider": "claude", "CLAUDE_CODE_REMOTE": "false", "AIL_MEMORY_AUTHORIZATION": None})
        for extra in extras:
            self.failure(self.client(endpoint, **extra), "not_executed")
        self.assertEqual(len(endpoint.calls), count)

    def test_invalid_local_configuration_or_request_never_dispatches(self) -> None:
        # Catches dispatch on a bad timeout, non-HTTPS/non-loopback URL, embedded credentials,
        # header injection, malformed or oversize request. Misses: TLS certificate handling.
        endpoint = self.endpoint()
        options: list[dict[str, str | None]] = [
            {"AIL_MEMORY_TIMEOUT_SECONDS": v} for v in ("nan", "inf", "-inf", "0", "0.5", "61", "oops", "")]
        options += [{"AIL_MEMORY_URL": "http://memory.invalid/memory"},
                    {"AIL_MEMORY_URL": "http://" + ".".join(["0"] * 4) + "/memory"}, {"AIL_MEMORY_URL": ""},
                    {"AIL_MEMORY_ALLOW_LOOPBACK": None},
                    {"AIL_MEMORY_URL": endpoint.url.replace("127.0.0.1", "user:password@127.0.0.1")},
                    {"AIL_MEMORY_AUTHORIZATION": "Bearer x\r\nX-Leak: y"}]
        for extra in options:
            with self.subTest(config=extra):
                self.failure(self.client(endpoint, **extra), "not_executed")
        for raw in ("", "{}", "[]", "null", "{", json.dumps(REQUEST) + " {}",
                    '{"version":2,"sql":"select 1","params":[]}',
                    '{"version":1,"sql":null,"params":[]}',
                    '{"version":1,"sql":"select 1","params":{}}',
                    '{"version":1,"sql":"select $1","params":[NaN]}',
                    json.dumps({"version": 1, "sql": "select $1", "params": ["x" * 65536]})):
            with self.subTest(raw_length=len(raw)):
                self.failure(self.client(endpoint, raw=raw), "not_executed")
        self.assertEqual(endpoint.calls, [])

    def test_malformed_or_oversized_response_is_unknown_not_success(self) -> None:
        # Catches a client that reports success for a malformed, non-v1 or >1 MiB response.
        # Misses: responses exactly at the limit (only limit+1 is sent).
        responses: list[Any] = [b"truncated {", b"", [], {"version": 2, "columns": [], "rows": []},
                                {"version": 1, "rows": []}, {"version": 1, "columns": [], "rows": {}},
                                {"version": 1, "columns": [None], "rows": []},
                                {"version": 1, "columns": ["x"], "rows": [[1, 2]]},
                                {"version": 1, "columns": ["x"], "rows": [{"x": 1}]},
                                b'{"version":1,"columns":["x"],"rows":[[NaN]]}', b" " * 1048577]
        for response in responses:
            with self.subTest(response=str(response)[:80]):
                endpoint = self.endpoint(response)
                self.failure(self.client(endpoint), "unknown")
                self.assertEqual(len(endpoint.calls), 1)

    def test_complete_json_with_incomplete_http_body_is_unknown(self) -> None:
        # Catches trusting a parseable body whose HTTP framing says bytes are missing; and any replay.
        # Misses: chunked-encoding truncation.
        refused = {"version": 1, "error": {"code": "refused", "message": "safe", "outcome": "rolled_back"}}
        for status, response in ((200, SUCCESS), (400, refused)):
            with self.subTest(status=status):
                endpoint = self.endpoint(response, status=status, mode="premature_eof")
                self.failure(self.client(endpoint), "unknown")
                self.assertEqual(len(endpoint.calls), 1, "uncertain response must never replay")

    def test_refusals_are_sanitized_and_keep_known_outcome(self) -> None:
        # Catches echoing server text (which may hold secrets) and collapsing not_executed/rolled_back/unknown.
        # Misses: refusal codes the server may add later.
        for status, outcome in ((401, "not_executed"), (403, "not_executed"), (503, "not_executed"),
                                (400, "rolled_back"), (500, "unknown")):
            response = {"version": 1, "error": {"code": "refused", "message": AUTH + PAYLOAD_MARKER,
                                                "outcome": outcome}}
            endpoint = self.endpoint(response, status=status)
            self.failure(self.client(endpoint), outcome)
            self.assertEqual(len(endpoint.calls), 1)
        for bad in ({"version": 1, "error": {"code": "x", "message": "x", "outcome": "committed"}},
                    b"Traceback: " + AUTH.encode()):
            self.failure(self.client(self.endpoint(bad, status=500)), "unknown")

    def test_redirects_never_forward_credentials(self) -> None:
        # Catches following a redirect (which would forward Authorization to another origin).
        # Misses: redirects to HTTPS origins (only loopback is reachable in tests).
        target = self.endpoint()
        for status in (301, 302, 303, 307, 308):
            source = self.endpoint({}, status=status, location=target.url)
            self.failure(self.client(source), "unknown")
            self.assertEqual(len(source.calls), 1)
        self.assertEqual(target.calls, [])

    def test_timeout_and_unreachable_endpoint_are_bounded_failures(self) -> None:
        # Catches an unbounded wait or a success claim when the endpoint is slow or absent.
        # Misses: DNS resolution time (loopback only).
        endpoint = self.endpoint(mode="delay")
        start = time.monotonic()
        self.failure(self.client(endpoint, AIL_MEMORY_TIMEOUT_SECONDS="1"), "unknown")
        self.assertLess(time.monotonic() - start, 3)
        self.assertEqual(len(endpoint.calls), 1)
        with contextlib.closing(socket.socket()) as sock:
            sock.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{sock.getsockname()[1]}/memory"
        result = self.client(endpoint, AIL_MEMORY_URL=url, AIL_MEMORY_TIMEOUT_SECONDS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn(json.loads(result.stderr)["error"]["outcome"], ("not_executed", "unknown"))


class Recovery(CloudCase):
    def test_lost_write_response_is_unknown_and_never_replayed(self) -> None:
        # Catches automatic retry/replay and any offline queue or credential written to disk.
        # Misses: replays a later session might attempt (no such code may exist; reviewed by hand).
        endpoint = self.endpoint(mode="drop")
        self.failure(self.client(endpoint), "unknown")
        time.sleep(0.15)
        self.assertEqual(len(endpoint.calls), 1)
        self.assertEqual(json.loads(endpoint.calls[0]["body"]), REQUEST)
        self.assertEqual(list(self.home.rglob("*")), [], "client persisted an offline queue or credential")


class Transport(CloudCase):
    def test_configured_http_proxy_receives_request(self) -> None:
        # Catches a client that ignores the host's proxy settings (the provider proxy injects credentials).
        # Misses: HTTPS CONNECT tunnelling through a real proxy.
        self.assertTrue(CLIENT.is_file(), "missing bin/ail-memory public client")
        endpoint = self.endpoint()
        proxy = self.endpoint()
        env = self.env(endpoint)
        env.update(HTTP_PROXY=proxy.url, http_proxy=proxy.url, NO_PROXY="", no_proxy="")
        result = subprocess.run([str(CLIENT)], input=json.dumps(REQUEST), text=True,
                                capture_output=True, env=env, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(endpoint.calls, [])
        self.assertEqual(len(proxy.calls), 1)
        self.assertEqual(proxy.calls[0]["path"], endpoint.url)
        self.assertEqual(proxy.calls[0]["headers"].get("Authorization"), AUTH)
        self.assertEqual(json.loads(proxy.calls[0]["body"]), REQUEST)

    def test_exponent_overflow_response_is_unconfirmed(self) -> None:
        # Catches accepting a non-finite number disguised as an exponent. Misses: other float edge cases.
        endpoint = self.endpoint(b'{"version":1,"columns":["x"],"rows":[[1e999]]}')
        self.failure(self.client(endpoint), "unknown")

    def test_success_http_status_cannot_claim_server_refusal_outcome(self) -> None:
        # Catches trusting a refusal body on HTTP 200. Misses: nothing else about status mapping.
        endpoint = self.endpoint({"version": 1, "error": {
            "code": "x", "message": "x", "outcome": "rolled_back"}}, status=200)
        self.failure(self.client(endpoint), "unknown")

    def test_completed_refusal_reporting_outlives_request_deadline(self) -> None:
        # Catches a request watchdog that fires during a slow diagnostic write and corrupts the outcome.
        # Misses: other kinds of output backpressure.
        self.assertTrue(CLIENT.is_file(), "missing bin/ail-memory public client")
        endpoint = self.endpoint({"version": 1, "error": {
            "code": "refused", "message": "unavailable", "outcome": "not_executed"}}, status=401)
        wrapper = """import runpy, sys, time
class SlowStderr:
    def write(self, value):
        if value.startswith("{"):
            time.sleep(1.1)
        return sys.__stderr__.write(value)
    def flush(self):
        sys.__stderr__.flush()
sys.stderr = SlowStderr()
runpy.run_path(sys.argv[1], run_name="__main__")
"""
        result = subprocess.run([sys.executable, "-c", wrapper, str(CLIENT)],
                                input=json.dumps(REQUEST), text=True, capture_output=True,
                                env=self.env(endpoint, AIL_MEMORY_TIMEOUT_SECONDS="1"), timeout=8)
        self.failure(result, "not_executed")
        self.assertEqual(len(endpoint.calls), 1)

    def test_incomplete_framing_never_confirms_json(self) -> None:
        # Catches confirming a body shorter than Content-Length on a keep-alive-less close.
        # Misses: chunked transfer encoding.
        self.assertTrue(CLIENT.is_file(), "missing bin/ail-memory public client")
        for status, body in ((200, SUCCESS), (403, {"version": 1, "error": {
                "code": "denied", "message": "refused", "outcome": "not_executed"}})):
            with self.subTest(status=status):
                calls: list[bytes] = []

                class Handler(http.server.BaseHTTPRequestHandler):
                    def do_POST(self, body: Any = body, status: int = status, calls: list[bytes] = calls) -> None:
                        calls.append(self.rfile.read(int(self.headers["Content-Length"])))
                        raw = json.dumps(body).encode()
                        self.send_response(status)
                        self.send_header("Content-Length", str(len(raw) + 100))
                        self.end_headers()
                        self.wfile.write(raw)
                        self.close_connection = True

                    def log_message(self, format: str, *args: Any) -> None:
                        pass

                server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                endpoint = type("Endpoint", (), {"url": f"http://127.0.0.1:{server.server_port}"})()
                try:
                    self.failure(self.client(endpoint), "unknown")
                    self.assertEqual(len(calls), 1)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()
