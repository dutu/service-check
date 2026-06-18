from __future__ import annotations

import unittest

from service_check.checks.public_ip_reputation.check import ProviderResult, _classified_result, _classify, _detect_public_ip, run
from service_check.models import CRIT, OK, UNKNOWN, CheckConfig
from service_check.runner import render_result_message


class PublicIpReputationTests(unittest.TestCase):
    def test_clean_is_final_verdict_for_unflagged_ip(self) -> None:
        verdict, confidence, sources = _classify([ProviderResult("ipapi_is", "clean", "medium")])

        self.assertEqual(verdict, "clean")
        self.assertEqual(confidence, "medium")
        self.assertEqual(sources, ["ipapi_is"])

    def test_clean_can_be_configured_as_failure(self) -> None:
        config = CheckConfig(
            section="public_ip_reputation",
            check="public_ip_reputation",
            options={"fail_on_verdicts": "clean"},
        )
        details = {
            "public_ip": "198.51.100.10",
            "verdict": "clean",
            "sources": "ipapi_is",
        }

        result = _classified_result(config, details, {})

        self.assertEqual(result.status, CRIT)
        self.assertEqual(result.details["problem_code"], "clean_detected")

    def test_failure_template_is_not_used_for_non_failing_verdict(self) -> None:
        config = CheckConfig(
            section="public_ip_reputation",
            check="public_ip_reputation",
            options={
                "fail_on_verdicts": "tor,vpn,proxy",
                "failure_message.clean_detected": "failure clean {public_ip}",
                "success_message": "success {verdict} {public_ip}",
            },
        )
        details = {
            "public_ip": "198.51.100.10",
            "verdict": "clean",
            "sources": "ipapi_is",
        }

        result = _classified_result(config, details, {})
        message = render_result_message(config, result, {**config.options, **result.details})

        self.assertEqual(result.status, OK)
        self.assertNotIn("problem_code", result.details)
        self.assertEqual(message, "success clean 198.51.100.10")

    def test_inconclusive_can_be_configured_as_failure(self) -> None:
        config = CheckConfig(
            section="public_ip_reputation",
            check="public_ip_reputation",
            options={"fail_on_verdicts": "inconclusive"},
        )
        details = {
            "public_ip": "198.51.100.10",
            "verdict": "inconclusive",
            "sources": "ipapi_is",
        }

        result = _classified_result(config, details, {})

        self.assertEqual(result.status, CRIT)
        self.assertEqual(result.details["problem_code"], "inconclusive_detected")

    def test_unknown_can_be_configured_as_failure(self) -> None:
        config = CheckConfig(
            section="public_ip_reputation",
            check="public_ip_reputation",
            options={"fail_on_verdicts": "unknown"},
        )
        details = {
            "public_ip": "198.51.100.10",
            "verdict": "unknown",
            "sources": "",
        }

        result = _classified_result(config, details, {})

        self.assertEqual(result.status, CRIT)
        self.assertEqual(result.details["problem_code"], "unknown_detected")

    def test_missing_public_ip_interface_fails_detection(self) -> None:
        config = CheckConfig(
            section="public_ip_reputation",
            check="public_ip_reputation",
            options={"public_ip_interface": "__service_check_missing_interface__"},
        )

        with self.assertRaisesRegex(ValueError, "network interface is not present"):
            _detect_public_ip(config, timeout=1)

    def test_missing_public_ip_interface_returns_specific_problem_code(self) -> None:
        config = CheckConfig(
            section="public_ip_reputation",
            check="public_ip_reputation",
            options={"public_ip_interface": "__service_check_missing_interface__"},
        )

        result = run(config, {})

        self.assertEqual(result.status, UNKNOWN)
        self.assertEqual(result.details["problem_code"], "public_ip_interface_missing")

    def test_public_ip_override_skips_interface_detection(self) -> None:
        config = CheckConfig(
            section="public_ip_reputation",
            check="public_ip_reputation",
            options={
                "public_ip": "8.8.8.8",
                "public_ip_interface": "__service_check_missing_interface__",
            },
        )

        self.assertEqual(_detect_public_ip(config, timeout=1), "8.8.8.8")


if __name__ == "__main__":
    unittest.main()
