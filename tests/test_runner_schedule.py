from __future__ import annotations

import unittest
from datetime import datetime, timezone

from service_check.models import CheckConfig, CheckDefaults
from service_check.runner import is_due


class RunnerScheduleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.defaults = CheckDefaults(
            notify_cmd=None,
            interval_minutes=5,
            timeout_seconds=2,
            retries=0,
            retry_delay_seconds=1,
            fail_after=1,
            notify_repeat_after_minutes=60,
            notify_on_recovery=True,
            notify_on_first_success=False,
        )

    def test_zero_interval_is_due_every_invocation(self) -> None:
        check_config = CheckConfig(
            section="machine_kuma_heartbeat",
            check="kuma_heartbeat",
            options={"interval_minutes": "0"},
        )
        previous = {"last_run_at": "2026-07-04T15:39:50Z"}
        now = datetime(2026, 7, 4, 15, 39, 51, tzinfo=timezone.utc)

        self.assertTrue(is_due(check_config, previous, self.defaults, now))

    def test_next_wall_clock_bucket_is_due_even_before_full_elapsed_interval(self) -> None:
        check_config = CheckConfig(
            section="machine_kuma_heartbeat",
            check="kuma_heartbeat",
            options={"interval_minutes": "1"},
        )
        previous = {"last_run_at": "2026-07-04T15:38:40Z"}
        now = datetime(2026, 7, 4, 15, 39, 39, tzinfo=timezone.utc)

        self.assertTrue(is_due(check_config, previous, self.defaults, now))

    def test_same_wall_clock_bucket_is_not_due_twice(self) -> None:
        check_config = CheckConfig(
            section="machine_kuma_heartbeat",
            check="kuma_heartbeat",
            options={"interval_minutes": "1"},
        )
        previous = {"last_run_at": "2026-07-04T15:39:10Z"}
        now = datetime(2026, 7, 4, 15, 39, 59, tzinfo=timezone.utc)

        self.assertFalse(is_due(check_config, previous, self.defaults, now))


if __name__ == "__main__":
    unittest.main()
