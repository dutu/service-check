from __future__ import annotations

import hashlib
import json
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4
from unittest.mock import patch

import requests
from requests.utils import parse_dict_header

from service_check.checks.monerod.check import _fetch_get_info


class MonerodRpcTests(unittest.TestCase):
    def test_digest_retry_reuses_connection(self) -> None:
        for algorithms in [("MD5",), ("MD5", "MD5-sess")]:
            with self.subTest(algorithms=algorithms), rpc_server(algorithms=algorithms) as server:
                result = _fetch_get_info("127.0.0.1", server.server_port, "user:secret:password", 2)

                self.assertEqual(result.error, "")
                self.assertEqual(result.payload, {"height": 123, "synchronized": True})
                self.assertEqual(len(server.requests), 2)
                first, retry = server.requests
                self.assertEqual(first["connection"], retry["connection"])
                self.assertEqual(first["authorization"], "")
                self.assertTrue(retry["authorization"].startswith("Digest "))
                for request in server.requests:
                    self.assertEqual(request["path"], "/json_rpc")
                    self.assertEqual(request["body"], {"jsonrpc": "2.0", "id": "0", "method": "get_info"})
                    self.assertEqual(request["user_agent"], "service-check")

    def test_wrong_credentials_return_rpc_error(self) -> None:
        with rpc_server(algorithms=("MD5", "MD5-sess")) as server:
            result = _fetch_get_info("127.0.0.1", server.server_port, "user:wrong", 2)
            self.assertIsNone(result.payload)
            self.assertIn("401", result.error)
            self.assertEqual(len(server.requests), 2)

    def test_rpc_without_authentication(self) -> None:
        with rpc_server() as server:
            result = _fetch_get_info("127.0.0.1", server.server_port, None, 2)
            self.assertEqual(result.error, "")
            self.assertEqual(result.payload["height"], 123)
            self.assertEqual(len(server.requests), 1)
            self.assertEqual(server.requests[0]["authorization"], "")

    def test_http_failure_returns_rpc_error(self) -> None:
        with rpc_server(status=500) as server:
            result = _fetch_get_info("127.0.0.1", server.server_port, None, 2)
            self.assertIsNone(result.payload)
            self.assertIn("500", result.error)

    def test_invalid_rpc_responses_return_errors(self) -> None:
        for body in [b"not JSON", b"[]", b"null", b"{}", b'{"result":null}', b'{"result":[]}']:
            with self.subTest(body=body), rpc_server(body=body) as server:
                result = _fetch_get_info("127.0.0.1", server.server_port, None, 2)
                self.assertIsNone(result.payload)
                self.assertTrue(result.error)

    def test_transport_failures_return_rpc_errors(self) -> None:
        for error in [requests.Timeout("timed out"), requests.ConnectionError("connection refused")]:
            with self.subTest(error=error), patch("requests.Session.post", side_effect=error) as post:
                result = _fetch_get_info("127.0.0.1", 18081, None, 0.25)
                self.assertIsNone(result.payload)
                self.assertEqual(result.error, str(error))
                self.assertEqual(post.call_args.kwargs["timeout"], 0.25)


@contextmanager
def rpc_server(*, algorithms=(), status=200, body=b'{"result":{"height":123,"synchronized":true}}'):
    """Serve Digest challenges with the nonce scoped to each TCP connection."""
    requests_seen = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection_id = uuid4().hex
            self.nonce = ""

        def log_message(self, *_args):
            pass

        def do_POST(self):
            request_body = self.rfile.read(int(self.headers["Content-Length"]))
            authorization = self.headers.get("Authorization", "")
            requests_seen.append({
                "connection": self.connection_id,
                "authorization": authorization,
                "path": self.path,
                "body": json.loads(request_body),
                "user_agent": self.headers.get("User-Agent"),
            })
            if algorithms and not self.authenticated(authorization):
                self.nonce = uuid4().hex
                self.send_response(401)
                for algorithm in algorithms:
                    self.send_header(
                        "WWW-Authenticate",
                        f'Digest qop="auth", algorithm={algorithm}, realm="monero-rpc", nonce="{self.nonce}", stale=false',
                    )
                response_body = b"Unauthorized"
            else:
                self.send_response(status)
                response_body = body
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def authenticated(self, authorization):
            if not authorization.startswith("Digest "):
                return False
            fields = parse_dict_header(authorization[7:])
            if not self.nonce or fields.get("nonce") != self.nonce:
                return False
            if fields.get("username") != "user" or fields.get("uri") != self.path:
                return False
            algorithm = fields.get("algorithm", "MD5")
            if algorithm not in algorithms or fields.get("qop") != "auth" or fields.get("nc") != "00000001":
                return False
            a1 = md5("user:monero-rpc:secret:password")
            if algorithm == "MD5-sess":
                a1 = md5(f"{a1}:{self.nonce}:{fields['cnonce']}")
            a2 = md5(f"POST:{self.path}")
            expected = md5(f"{a1}:{self.nonce}:{fields['nc']}:{fields['cnonce']}:auth:{a2}")
            return fields.get("response") == expected

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.daemon_threads = True
        server.requests = requests_seen
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=2)


def md5(value: str) -> str:
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()


if __name__ == "__main__":
    unittest.main()
