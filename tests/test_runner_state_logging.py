from __future__ import annotations

import unittest

from service_check.models import CRIT, OK, CheckConfig, CheckDefaults, CheckResult, GlobalConfig
from service_check.runner import process_result


class RunnerStateLoggingTest(unittest.TestCase):
    def test_process_result_logs_check_state_change(self) -> None:
        checks_state: dict[str, object] = {}

        with self.assertLogs("service_check.runner", level="INFO") as logs:
            process_result(
                _global_config(),
                _defaults(),
                _check_config(),
                CheckResult("wallet_rpc", CRIT, "wallet RPC down", details={"problem_code": "port_unreachable"}),
                checks_state,
                dry_run=False,
                no_notify=False,
                duration_ms=12,
            )
            process_result(
                _global_config(),
                _defaults(),
                _check_config(),
                CheckResult("wallet_rpc", CRIT, "wallet RPC still down", details={"problem_code": "port_unreachable"}),
                checks_state,
                dry_run=False,
                no_notify=False,
                duration_ms=11,
            )
            process_result(
                _global_config(),
                _defaults(),
                _check_config(),
                CheckResult("wallet_rpc", OK, "wallet RPC reachable"),
                checks_state,
                dry_run=False,
                no_notify=False,
                duration_ms=8,
            )
            process_result(
                _global_config(),
                _defaults(),
                _check_config(),
                CheckResult("wallet_rpc", OK, "wallet RPC reachable again"),
                checks_state,
                dry_run=False,
                no_notify=False,
                duration_ms=7,
            )

        state_change_logs = [entry for entry in logs.output if "check_state_change" in entry]
        self.assertEqual(len(state_change_logs), 2)
        self.assertIn("section=wallet_rpc", state_change_logs[0])
        self.assertIn("changed=status,problem,problem_code", state_change_logs[0])
        self.assertIn("status=-->CRIT", state_change_logs[0])
        self.assertIn("previous_run_at=-", state_change_logs[0])
        self.assertIn("seconds_since_previous_run=-", state_change_logs[0])
        self.assertIn("problem_code=-->port_unreachable", state_change_logs[0])
        self.assertIn("changed=status,problem,problem_code", state_change_logs[1])
        self.assertIn("status=CRIT->OK", state_change_logs[1])
        self.assertIn("problem=True->False", state_change_logs[1])
        self.assertIn("problem_code=port_unreachable->-", state_change_logs[1])
        self.assertIn("previous_run_at=", state_change_logs[1])
        self.assertIn("current_run_at=", state_change_logs[1])
        self.assertNotIn("seconds_since_previous_run=-", state_change_logs[1])


def _global_config() -> GlobalConfig:
    return GlobalConfig(
        hostname="host",
        state_file="state.json",
        lock_file="state.json.lock",
        max_run_seconds=50,
        max_lock_hold_minutes=2,
    )


def _defaults() -> CheckDefaults:
    return CheckDefaults(
        notify_cmd=None,
        interval_minutes=5,
        timeout_seconds=5,
        retries=0,
        retry_delay_seconds=1,
        fail_after=1,
        notify_repeat_after_minutes=60,
        notify_on_recovery=True,
        notify_on_first_success=False,
    )


def _check_config() -> CheckConfig:
    return CheckConfig(
        section="wallet_rpc",
        check="tcp_port",
        options={
            "failure_message.port_unreachable": "wallet RPC down",
            "success_message": "wallet RPC reachable",
        },
    )


if __name__ == "__main__":
    unittest.main()
