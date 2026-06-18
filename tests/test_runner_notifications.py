from __future__ import annotations

import unittest
from unittest.mock import patch

from service_check.models import CRIT, OK, WARN, CheckConfig, CheckDefaults, CheckResult, GlobalConfig
from service_check.runner import process_result


class RunnerNotificationTests(unittest.TestCase):
    def test_warn_after_failure_does_not_clear_recovery(self) -> None:
        sent_messages: list[str] = []
        checks_state: dict[str, object] = {}
        global_config = GlobalConfig(
            hostname="host",
            state_file="state.json",
            lock_file="state.json.lock",
            max_run_seconds=50,
            max_lock_hold_minutes=2,
        )
        defaults = CheckDefaults(
            notify_cmd="notify",
            interval_minutes=5,
            timeout_seconds=5,
            retries=0,
            retry_delay_seconds=1,
            fail_after=1,
            notify_repeat_after_minutes=60,
            notify_on_recovery=True,
            notify_on_first_success=False,
        )
        check_config = CheckConfig(
            section="monerod",
            check="monerod",
            options={
                "failure_message.sync_pending": "sync pending",
                "success_message": "synced",
            },
        )

        def fake_send_notification(_notify_cmd: str, message: str, _dry_run: bool) -> None:
            sent_messages.append(message)
            return None

        with patch("service_check.runner.send_notification", fake_send_notification):
            process_result(
                global_config,
                defaults,
                check_config,
                CheckResult("monerod", CRIT, "stopped", details={"problem_code": "service_inactive"}),
                checks_state,
                dry_run=False,
                no_notify=False,
                duration_ms=1,
            )
            process_result(
                global_config,
                defaults,
                check_config,
                CheckResult("monerod", WARN, "sync pending", details={"problem_code": "sync_pending"}),
                checks_state,
                dry_run=False,
                no_notify=False,
                duration_ms=1,
            )
            self.assertTrue(checks_state["monerod"]["last_problem"])
            process_result(
                global_config,
                defaults,
                check_config,
                CheckResult("monerod", OK, "synced"),
                checks_state,
                dry_run=False,
                no_notify=False,
                duration_ms=1,
            )

        self.assertEqual(sent_messages, ["stopped", "sync pending", "synced"])
        self.assertFalse(checks_state["monerod"]["last_problem"])


if __name__ == "__main__":
    unittest.main()
