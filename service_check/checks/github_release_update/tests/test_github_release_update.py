from __future__ import annotations

import unittest

from service_check.checks.github_release_update.check import run
from service_check.models import OK, WARN, CheckConfig


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
