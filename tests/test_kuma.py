from __future__ import annotations

import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from service_check.kuma import USER_AGENT, push_kuma


class FakeResponse:
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return b"ok"


class KumaPushTest(unittest.TestCase):
    def test_push_adds_status_message_and_headers(self) -> None:
        with patch("service_check.kuma.urlopen", return_value=FakeResponse()) as urlopen:
            error = push_kuma(
                "https://kuma.example.com/api/push/token?ping=60",
                "OK",
                "machine heartbeat OK",
                timeout=2,
            )

        self.assertIsNone(error)
        request = urlopen.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["ping"], ["60"])
        self.assertEqual(query["status"], ["up"])
        self.assertEqual(query["msg"], ["machine heartbeat OK"])
        self.assertEqual(request.headers["User-agent"], USER_AGENT)
        self.assertEqual(request.headers["Accept"], "*/*")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 2)

    def test_push_reports_http_error_body(self) -> None:
        http_error = HTTPError(
            "https://kuma.example.com/api/push/token",
            403,
            "Forbidden",
            {},
            BytesIO(b"blocked user agent"),
        )

        with patch("service_check.kuma.urlopen", side_effect=http_error):
            error = push_kuma(
                "https://kuma.example.com/api/push/token",
                "OK",
                "machine heartbeat OK",
                timeout=2,
            )

        self.assertEqual(error, "HTTP Error 403: Forbidden: blocked user agent")


if __name__ == "__main__":
    unittest.main()
