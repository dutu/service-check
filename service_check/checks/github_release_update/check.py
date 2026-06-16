from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from service_check import __version__
from service_check.models import OK, UNKNOWN, WARN, CheckConfig, CheckResult

DEFAULT_REPOSITORY = "dutu/service-check"
CHECK_METADATA = {
    "description": "Compares the installed service-check version with a GitHub release or expected version.",
    "statuses": {
        OK: "Installed version matches the expected/latest version.",
        WARN: "Installed version differs from the expected/latest version.",
        UNKNOWN: "Version config is invalid or latest release could not be fetched.",
    },
    "details": {
        "current_version": "Installed or configured current version.",
        "expected_version": "Expected/latest version used for comparison.",
        "latest_version": "Latest version resolved from config or GitHub.",
        "available_version": "Alias for latest_version for notification templates.",
        "repository": "GitHub repository in owner/name form.",
        "error": "Validation/fetch/parse error text; present on UNKNOWN results.",
    },
}


def run(config: CheckConfig) -> CheckResult:
    current_version = config.get("current_version", __version__) or __version__
    expected_version = config.get("expected_version")
    repository = config.get("repository") or config.get("repo") or DEFAULT_REPOSITORY
    latest_version = expected_version
    details = {
        "current_version": current_version,
        "expected_version": expected_version or "",
        "latest_version": "",
        "available_version": "",
        "repository": repository,
    }

    if not latest_version:
        try:
            latest_version = _fetch_latest_release_version(
                repository=repository,
                api_url=config.get("api_url"),
                timeout=config.get_float("timeout_seconds", 10.0),
            )
        except ValueError as exc:
            return CheckResult(
                name=config.section,
                status=UNKNOWN,
                message=f"github_release_update check has invalid config: {exc}",
                details={**details, "error": str(exc)},
            )
        except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
            return CheckResult(
                name=config.section,
                status=UNKNOWN,
                message=f"github_release_update check could not fetch latest GitHub release: {exc}",
                details={**details, "error": str(exc)},
            )

    details["latest_version"] = latest_version
    details["available_version"] = latest_version
    details["expected_version"] = latest_version

    try:
        comparison = _compare_versions(current_version, latest_version)
    except ValueError as exc:
        config_source = "version config" if expected_version else "GitHub release version"
        return CheckResult(
            name=config.section,
            status=UNKNOWN,
            message=f"github_release_update check has invalid {config_source}: {exc}",
            details={**details, "error": str(exc)},
        )

    if comparison < 0:
        return CheckResult(
            name=config.section,
            status=WARN,
            message=f"service-check {current_version} is behind available version {latest_version}",
            details=details,
        )
    if comparison > 0:
        return CheckResult(
            name=config.section,
            status=WARN,
            message=f"service-check {current_version} is newer than available version {latest_version}",
            details=details,
        )

    return CheckResult(
        name=config.section,
        status=OK,
        message=f"service-check {current_version} is up-to-date",
        details=details,
    )


def _fetch_latest_release_version(repository: str, api_url: str | None, timeout: float) -> str:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError(f"expected GitHub repository as owner/name, got {repository!r}")

    url = api_url or f"https://api.github.com/repos/{repository}/releases/latest"
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "service-check",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    tag_name = payload.get("tag_name")
    if not isinstance(tag_name, str) or not tag_name.strip():
        raise ValueError("latest GitHub release response did not include tag_name")
    return tag_name.strip()


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
