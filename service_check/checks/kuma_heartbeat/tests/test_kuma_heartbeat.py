from __future__ import annotations

import unittest
from unittest.mock import patch

from service_check.checks.kuma_heartbeat.check import run
from service_check.models import CRIT, OK, UNKNOWN, CheckConfig


class KumaHeartbeatCheckTest(unittest.TestCase):
    def test_push_success_is_ok(self) -> None:
        config = CheckConfig(
            section="machine_kuma_heartbeat",
            check="kuma_heartbeat",
            options={"heartbeat_url": "https://kuma.example.com/api/push/token"},
        )

        with patch("service_check.checks.kuma_heartbeat.check.socket.gethostname", return_value="host-a"):
            with patch("service_check.checks.kuma_heartbeat.check.push_kuma", return_value=None) as push:
                result = run(config)

        self.assertEqual(result.name, "machine_kuma_heartbeat")
        self.assertEqual(result.status, OK)
        self.assertEqual(result.details["pushed_message"], "host-a service-check Kuma heartbeat OK")
        push.assert_called_once_with(
            "https://kuma.example.com/api/push/token",
            OK,
            "host-a service-check Kuma heartbeat OK",
            5.0,
        )

    def test_push_failure_is_critical(self) -> None:
        config = CheckConfig(
            section="machine_kuma_heartbeat",
            check="kuma_heartbeat",
            options={"heartbeat_url": "https://kuma.example.com/api/push/token"},
        )

        with patch("service_check.checks.kuma_heartbeat.check.push_kuma", return_value="HTTP Error 403: Forbidden"):
            result = run(config)

        self.assertEqual(result.status, CRIT)
        self.assertEqual(result.details["problem_code"], "kuma_push_failed")
        self.assertEqual(result.details["error"], "HTTP Error 403: Forbidden")

    def test_missing_heartbeat_url_is_unknown(self) -> None:
        result = run(CheckConfig(section="machine_kuma_heartbeat", check="kuma_heartbeat", options={}))

        self.assertEqual(result.status, UNKNOWN)
        self.assertEqual(result.details["problem_code"], "missing_heartbeat_url")

    def test_custom_push_message_is_rendered(self) -> None:
        config = CheckConfig(
            section="machine_kuma_heartbeat",
            check="kuma_heartbeat",
            options={
                "heartbeat_url": "https://kuma.example.com/api/push/token",
                "heartbeat_message": "{hostname} {section} OK",
            },
        )

        with patch("service_check.checks.kuma_heartbeat.check.socket.gethostname", return_value="host-a"):
            with patch("service_check.checks.kuma_heartbeat.check.push_kuma", return_value=None):
                result = run(config)

        self.assertEqual(result.details["pushed_message"], "host-a machine_kuma_heartbeat OK")


if __name__ == "__main__":
    unittest.main()
