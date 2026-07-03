from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from service_check.checks.ping.check import run
from service_check.models import CRIT, OK, UNKNOWN, CheckConfig


class PingCheckTests(unittest.TestCase):
    def test_ping_success_is_ok(self) -> None:
        with patch("service_check.checks.ping.check.subprocess.run") as subprocess_run:
            subprocess_run.return_value = subprocess.CompletedProcess(
                args=["ping"],
                returncode=0,
                stdout="reply",
                stderr="",
            )

            result = run(_config({"host": "192.0.2.1", "count": "1", "timeout_seconds": "2"}))

        self.assertEqual(result.status, OK)
        self.assertEqual(result.details["host"], "192.0.2.1")
        self.assertEqual(result.details["count"], 1)
        self.assertEqual(result.details["exit_code"], 0)

    def test_ping_failure_is_critical(self) -> None:
        with patch("service_check.checks.ping.check.subprocess.run") as subprocess_run:
            subprocess_run.return_value = subprocess.CompletedProcess(
                args=["ping"],
                returncode=1,
                stdout="100% packet loss",
                stderr="",
            )

            result = run(_config({"host": "192.0.2.1"}))

        self.assertEqual(result.status, CRIT)
        self.assertEqual(result.details["problem_code"], "ping_failed")
        self.assertEqual(result.details["error"], "100% packet loss")

    def test_missing_host_is_unknown(self) -> None:
        result = run(_config({}))

        self.assertEqual(result.status, UNKNOWN)
        self.assertEqual(result.details["problem_code"], "missing_host")

    def test_invalid_count_is_unknown(self) -> None:
        result = run(_config({"host": "192.0.2.1", "count": "0"}))

        self.assertEqual(result.status, UNKNOWN)
        self.assertEqual(result.details["problem_code"], "invalid_count")

    def test_missing_ping_command_is_unknown(self) -> None:
        with patch("service_check.checks.ping.check.subprocess.run", side_effect=FileNotFoundError):
            result = run(_config({"host": "192.0.2.1"}))

        self.assertEqual(result.status, UNKNOWN)
        self.assertEqual(result.details["problem_code"], "ping_command_missing")


def _config(options: dict[str, str]) -> CheckConfig:
    return CheckConfig(section="gateway_ping", check="ping", options=options)


if __name__ == "__main__":
    unittest.main()
