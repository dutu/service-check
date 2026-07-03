from __future__ import annotations

import platform
import subprocess
import time

from service_check.models import CRIT, OK, UNKNOWN, CheckConfig, CheckResult

CHECK_METADATA = {
    "description": "Checks whether a host responds to ICMP echo requests.",
    "statuses": {
        OK: "At least one ping reply was received.",
        CRIT: "No ping reply was received before the command failed or timed out.",
        UNKNOWN: "Required config is missing or invalid, or no ping command is available.",
    },
    "details": {
        "problem_code": "Primary machine-readable problem reason.",
        "problem_codes": "List of machine-readable problem reasons.",
        "host": "Configured hostname or IP address.",
        "count": "Number of ICMP echo requests sent.",
        "timeout_seconds": "Per-command timeout used by the check.",
        "elapsed_ms": "Ping command duration in milliseconds.",
        "exit_code": "Ping process exit code; present when the command runs.",
        "error": "Config or runtime error text; present on failure.",
    },
}


def run(config: CheckConfig) -> CheckResult:
    host = config.get("host")
    if not host:
        return _unknown(config, "missing_host", "ping check requires host")

    try:
        count = config.get_int("count", 2)
    except ValueError:
        return _unknown(config, "invalid_count", "ping check requires numeric count", {"host": host})
    if count <= 0:
        return _unknown(config, "invalid_count", "ping check requires count greater than 0", {"host": host})

    try:
        timeout_seconds = config.get_float("timeout_seconds", 5.0)
    except ValueError:
        return _unknown(config, "invalid_timeout", "ping check requires numeric timeout_seconds", {"host": host})
    if timeout_seconds <= 0:
        return _unknown(
            config,
            "invalid_timeout",
            "ping check requires timeout_seconds greater than 0",
            {"host": host, "count": count},
        )

    command = _ping_command(host, count, timeout_seconds)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=_process_timeout_seconds(count, timeout_seconds),
        )
    except FileNotFoundError:
        return _unknown(
            config,
            "ping_command_missing",
            "ping command is not available",
            {"host": host, "count": count, "timeout_seconds": timeout_seconds},
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return CheckResult(
            name=config.section,
            status=CRIT,
            message=f"Ping {host} timed out",
            details={
                "host": host,
                "count": count,
                "timeout_seconds": timeout_seconds,
                "elapsed_ms": elapsed_ms,
                "error": str(exc),
                "problem_code": "ping_failed",
                "problem_codes": ["ping_failed"],
            },
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    details = {
        "host": host,
        "count": count,
        "timeout_seconds": timeout_seconds,
        "elapsed_ms": elapsed_ms,
        "exit_code": completed.returncode,
    }
    if completed.returncode == 0:
        return CheckResult(
            name=config.section,
            status=OK,
            message=f"Ping {host} succeeded",
            details=details,
        )

    error = _last_output_line(completed.stderr) or _last_output_line(completed.stdout) or "ping failed"
    details["error"] = error
    details["problem_code"] = "ping_failed"
    details["problem_codes"] = ["ping_failed"]
    return CheckResult(
        name=config.section,
        status=CRIT,
        message=f"Ping {host} failed",
        details=details,
    )


def _ping_command(host: str, count: int, timeout_seconds: float) -> list[str]:
    system = platform.system().lower()
    if system == "windows":
        return ["ping", "-n", str(count), "-w", str(int(timeout_seconds * 1000)), host]
    if system == "darwin":
        return ["ping", "-c", str(count), "-W", str(int(timeout_seconds * 1000)), host]
    return ["ping", "-c", str(count), "-W", str(max(1, int(timeout_seconds))), host]


def _process_timeout_seconds(count: int, timeout_seconds: float) -> float:
    return max(timeout_seconds * count + 1.0, timeout_seconds + 1.0)


def _last_output_line(output: str) -> str | None:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    return lines[-1]


def _unknown(
    config: CheckConfig,
    problem_code: str,
    message: str,
    details: dict[str, object] | None = None,
) -> CheckResult:
    result_details = dict(details or {})
    result_details["problem_code"] = problem_code
    result_details["problem_codes"] = [problem_code]
    return CheckResult(
        name=config.section,
        status=UNKNOWN,
        message=message,
        details=result_details,
    )
