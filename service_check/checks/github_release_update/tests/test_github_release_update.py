from __future__ import annotations

import unittest

from service_check.checks.github_release_update.check import run
from service_check.models import OK, UNKNOWN, WARN, CheckConfig


class GithubReleaseUpdateTest(unittest.TestCase):
    def test_newer_local_version_uses_tag_style_message(self) -> None:
        result = run(_config(current_version="0.6.0", expected_version="v0.5.0"))

        self.assertEqual(result.status, WARN)
        self.assertEqual(result.message, "service-check v0.6.0 is newer than available version v0.5.0")
        self.assertEqual(result.details["current_version"], "0.6.0")
        self.assertEqual(result.details["current_version_tag"], "v0.6.0")
        self.assertEqual(result.details["available_version"], "v0.5.0")
        self.assertEqual(result.details["available_version_tag"], "v0.5.0")

    def test_older_local_version_uses_tag_style_message(self) -> None:
        result = run(_config(current_version="0.5.0", expected_version="v0.6.0"))

        self.assertEqual(result.status, WARN)
        self.assertEqual(result.message, "service-check v0.5.0 is behind available version v0.6.0")

    def test_equal_version_uses_tag_style_message(self) -> None:
        result = run(_config(current_version="0.6.0", expected_version="v0.6.0"))

        self.assertEqual(result.status, OK)
        self.assertEqual(result.message, "service-check v0.6.0 is up-to-date")

    def test_development_version_is_newer_than_previous_final(self) -> None:
        result = run(_config(current_version="0.6.0.dev0", expected_version="v0.5.0"))

        self.assertEqual(result.status, WARN)
        self.assertEqual(result.message, "service-check v0.6.0.dev0 is newer than available version v0.5.0")

    def test_development_version_is_older_than_beta(self) -> None:
        result = run(_config(current_version="0.6.0.dev0", expected_version="v0.6.0b1"))

        self.assertEqual(result.status, WARN)
        self.assertEqual(result.message, "service-check v0.6.0.dev0 is behind available version v0.6.0b1")

    def test_beta_version_is_older_than_final(self) -> None:
        result = run(_config(current_version="0.6.0b1", expected_version="v0.6.0"))

        self.assertEqual(result.status, WARN)
        self.assertEqual(result.message, "service-check v0.6.0b1 is behind available version v0.6.0")

    def test_invalid_pep_440_version_is_unknown(self) -> None:
        result = run(_config(current_version="next", expected_version="v0.6.0"))

        self.assertEqual(result.status, UNKNOWN)
        self.assertEqual(result.details["problem_code"], "invalid_version")
        self.assertEqual(result.details["error"], "expected PEP 440 version, got 'next'")


def _config(current_version: str, expected_version: str) -> CheckConfig:
    return CheckConfig(
        section="github_release_update",
        check="github_release_update",
        options={
            "current_version": current_version,
            "expected_version": expected_version,
        },
    )


if __name__ == "__main__":
    unittest.main()
