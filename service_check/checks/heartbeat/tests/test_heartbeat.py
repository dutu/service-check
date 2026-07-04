from __future__ import annotations

import unittest

from service_check.checks.heartbeat.check import run
from service_check.models import OK, CheckConfig


class HeartbeatCheckTest(unittest.TestCase):
    def test_heartbeat_is_ok(self) -> None:
        result = run(CheckConfig(section="machine_heartbeat", check="heartbeat", options={}))

        self.assertEqual(result.name, "machine_heartbeat")
        self.assertEqual(result.status, OK)
        self.assertEqual(result.message, "service-check heartbeat OK")
        self.assertTrue(result.details["heartbeat"])
