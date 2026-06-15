from __future__ import annotations

import re

from service_check import __version__
from service_check.models import OK, UNKNOWN, WARN, CheckConfig, CheckResult


def run(config: CheckConfig) -> CheckResult:
    current_version = config.get("current_version", __version__) or __version__
    expected_version = config.get("expected_version")
    details = {
        "current_version": current_version,
        "expected_version": expected_version or "",
        "latest_version": expected_version or "",
        "available_version": expected_version or "",
    }

    if expected_version:
        try:
            comparison = _compare_versions(current_version, expected_version)
        except ValueError as exc:
            return CheckResult(
                name=config.section,
                status=UNKNOWN,
                message=f"github_release_update check has invalid version config: {exc}",
                details={**details, "error": str(exc)},
            )
        if comparison < 0:
            return CheckResult(
                name=config.section,
                status=WARN,
                message=f"service-check {current_version} is behind available version {expected_version}",
                details=details,
            )

    return CheckResult(
        name=config.section,
        status=OK,
        message=f"service-check {current_version} is up-to-date",
        details=details,
    )


def _normalize_version(version: str) -> str:
    return version.strip().removeprefix("v").removeprefix("V")


def _compare_versions(left: str, right: str) -> int:
    left_parts = _parse_numeric_version(left)
    right_parts = _parse_numeric_version(right)
    max_length = max(len(left_parts), len(right_parts))
    padded_left = left_parts + [0] * (max_length - len(left_parts))
    padded_right = right_parts + [0] * (max_length - len(right_parts))
    if padded_left < padded_right:
        return -1
    if padded_left > padded_right:
        return 1
    return 0


def _parse_numeric_version(version: str) -> list[int]:
    normalized = _normalize_version(version)
    match = re.fullmatch(r"\d+(?:\.\d+)*", normalized)
    if not match:
        raise ValueError(f"expected numeric dotted version, got {version!r}")
    return [int(part) for part in normalized.split(".")]
