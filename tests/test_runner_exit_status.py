from __future__ import annotations

import io
import json
import logging
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from service_check.cli import main
from service_check.config import load_config
from service_check.models import CRIT, OK, UNKNOWN, WARN, CheckResult
from service_check.runner import run


class RunnerExitStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.directory = Path(temp_dir.name)
        self.config_path = self.directory / "service-check.ini"
        self.state_path = self.directory / "state.json"
        self.write_config()
        root = logging.getLogger()
        self.addCleanup(root.setLevel, root.level)

    def write_config(self, sections: tuple[str, ...] = ("probe",), defaults: str = "") -> None:
        self.config_path.write_text(
            f"[global]\nstate_file={self.state_path}\n"
            "[default]\nretries=0\nnotify_cmd=notify\n"
            f"{defaults}\n"
            + "".join(
                f"[{section}]\nenabled=1\ncheck=tcp_port\nhost=localhost\nport=1234\n"
                f"kuma_push_url=https://kuma.example/api/push/{section}\n"
                for section in sections
            ),
            encoding="utf-8",
        )

    def invoke(self, *args: str) -> int:
        return main(["--config", str(self.config_path), *args])

    def test_completed_runs_return_zero_and_preserve_results(self) -> None:
        for status in (OK, WARN, CRIT, UNKNOWN):
            for mode in ("--all", "--results-for", "--dry-run"):
                with self.subTest(status=status, mode=mode):
                    self.state_path.unlink(missing_ok=True)
                    args = [mode, "all"] if mode == "--results-for" else [mode]
                    output = io.StringIO()
                    response = MagicMock()
                    with (
                        patch("service_check.runner.get_check", return_value=lambda config: CheckResult(
                            config.section, status, "observed", state={"sample": status}
                        )),
                        patch("service_check.runner.send_notification", return_value=None) as notify,
                        patch("service_check.kuma.urlopen", return_value=response) as urlopen,
                        self.assertLogs(level="DEBUG") as logs,
                        redirect_stdout(output),
                    ):
                        exit_code = self.invoke(*args, "--verbose")
                    self.assertEqual(exit_code, 0)
                    self.assertTrue(any(f"run_end worst_status={status} exit_code=0" in line for line in logs.output))
                    if mode in ("--results-for", "--dry-run"):
                        self.assertIn(f"probe: {status} - observed", output.getvalue())
                    if mode == "--dry-run":
                        self.assertFalse(self.state_path.exists())
                        urlopen.assert_not_called()
                        if status in (CRIT, UNKNOWN):
                            notify.assert_called_once_with("notify", "observed", True)
                    else:
                        state = json.loads(self.state_path.read_text())["checks"]["probe"]
                        self.assertEqual(state["last_result"]["status"], status)
                        self.assertEqual(state["check_state"], {"sample": status})
                        self.assertEqual(state["last_problem"], status in (CRIT, UNKNOWN))
                        query = parse_qs(urlsplit(urlopen.call_args.args[0].full_url).query)
                        self.assertEqual(query["status"], ["down" if status in (CRIT, UNKNOWN) else "up"])
                        if status in (CRIT, UNKNOWN):
                            notify.assert_called_once_with("notify", "observed", False)
                        else:
                            notify.assert_not_called()

    def test_mixed_results_keep_worst_status_independent_of_order(self) -> None:
        for statuses in ((CRIT, UNKNOWN, WARN, OK), (OK, WARN, UNKNOWN, CRIT)):
            with self.subTest(statuses=statuses):
                sections = tuple(f"probe{index}" for index in range(len(statuses)))
                self.write_config(sections)
                results = [CheckResult(section, status, status) for section, status in zip(sections, statuses)]
                output = io.StringIO()
                with (
                    patch("service_check.runner.run_one_check", side_effect=results),
                    patch("service_check.runner.send_notification", return_value=None),
                    patch("service_check.runner.push_kuma", return_value=None) as kuma,
                    self.assertLogs(level="DEBUG") as logs,
                    redirect_stdout(output),
                ):
                    self.assertEqual(self.invoke("--results-for", "all", "--verbose"), 0)
                self.assertTrue(any("run_end worst_status=CRIT exit_code=0" in line for line in logs.output))
                state = json.loads(self.state_path.read_text())["checks"]
                for section, status, call in zip(sections, statuses, kuma.call_args_list):
                    self.assertIn(f"{section}: {status} - {status}", output.getvalue())
                    self.assertEqual(state[section]["last_result"]["status"], status)
                    self.assertEqual(call.args[1], status)

    def test_retries_threshold_repeat_suppression_and_recovery(self) -> None:
        self.write_config(defaults="fail_after=2\nnotify_repeat_after_minutes=60\n")
        # Enable one immediate retry without duplicating the INI key.
        self.config_path.write_text(self.config_path.read_text().replace("retries=0", "retries=1"))
        results = [CheckResult("probe", status, status) for status in (CRIT,) * 6 + (OK,)]
        with (
            patch("service_check.runner.run_one_check", side_effect=results) as check,
            patch("service_check.runner.time.sleep") as sleep,
            patch("service_check.runner.send_notification", return_value=None) as notify,
            patch("service_check.runner.push_kuma", return_value=None) as kuma,
        ):
            for failures, notifications in ((1, 0), (2, 1), (3, 1), (0, 2)):
                self.assertEqual(run(load_config(str(self.config_path)), run_all=True), 0)
                state = json.loads(self.state_path.read_text())["checks"]["probe"]
                self.assertEqual(state["consecutive_failures"], failures)
                self.assertEqual(state["last_problem"], failures > 0)
                self.assertEqual(notify.call_count, notifications)
        self.assertEqual(check.call_count, 7)
        self.assertEqual(sleep.call_count, 3)
        self.assertEqual([call.args[1] for call in notify.call_args_list], [CRIT, OK])
        self.assertEqual([call.args[1] for call in kuma.call_args_list], [CRIT, CRIT, CRIT, OK])

    def test_check_exception_is_unknown_and_run_succeeds(self) -> None:
        with (
            patch("service_check.runner.get_check", side_effect=RuntimeError("broken check")),
            patch("service_check.runner.send_notification", return_value=None),
            patch("service_check.runner.push_kuma", return_value=None),
            self.assertLogs("service_check.runner", level="ERROR"),
        ):
            self.assertEqual(self.invoke("--all"), 0)
        state = json.loads(self.state_path.read_text())["checks"]["probe"]
        self.assertEqual(state["last_result"]["status"], UNKNOWN)

    def test_configuration_error_returns_two(self) -> None:
        self.config_path.write_text("[global]\nmax_run_seconds=invalid\n", encoding="utf-8")
        with self.assertLogs(level="ERROR"):
            self.assertEqual(self.invoke(), 2)

    def test_missing_section_returns_two(self) -> None:
        with self.assertLogs(level="ERROR"):
            self.assertEqual(self.invoke("--results-for", "missing"), 2)

    def test_runner_failures_return_two(self) -> None:
        for target, error in (
            ("service_check.state.StateStore.load", OSError("cannot read state")),
            ("service_check.state.StateStore.save", OSError("cannot save state")),
            ("service_check.state._lock_file", TimeoutError("cannot lock state")),
            ("service_check.runner.process_selected_checks", RuntimeError("runner failed")),
        ):
            with (
                self.subTest(target=target),
                patch(target, side_effect=error),
                patch("service_check.runner.run_one_check", return_value=CheckResult("probe", CRIT, "down")),
                patch("service_check.runner.send_notification", return_value=None),
                patch("service_check.runner.push_kuma", return_value=None),
                self.assertLogs(level="ERROR"),
            ):
                self.assertEqual(self.invoke("--all"), 2)


if __name__ == "__main__":
    unittest.main()
