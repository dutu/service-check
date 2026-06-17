from __future__ import annotations

import socket
import time

from service_check.models import CRIT, OK, UNKNOWN, CheckConfig, CheckResult

CHECK_METADATA = {
    "description": "Checks whether a TCP connection can be established to a host and port.",
    "statuses": {
        OK: "TCP connection succeeded.",
        CRIT: "TCP connection failed.",
        UNKNOWN: "Required config is missing or invalid.",
    },
    "details": {
        "problem_code": "Primary machine-readable problem reason.",
        "problem_codes": "List of machine-readable problem reasons.",
        "host": "Configured hostname or IP address.",
        "port": "Configured TCP port number.",
        "timeout_seconds": "Connection timeout used by the check.",
        "elapsed_ms": "Connection attempt duration in milliseconds.",
        "error": "Socket/config error text; present on failure or invalid port.",
    },
}


def run(config: CheckConfig) -> CheckResult:
    host = config.get("host")
    port_value = config.get("port")
    timeout_seconds = config.get_float("timeout_seconds", 5.0)

    if not host:
        return CheckResult(
            name=config.section,
            status=UNKNOWN,
            message="tcp_port check requires host",
            details={"problem_code": "missing_host", "problem_codes": ["missing_host"]},
        )

    try:
        port = int(port_value or "")
    except ValueError:
        return CheckResult(
            name=config.section,
            status=UNKNOWN,
            message="tcp_port check requires numeric port",
            details={
                "host": host,
                "port": port_value,
                "problem_code": "invalid_port",
                "problem_codes": ["invalid_port"],
            },
        )

    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return CheckResult(
                name=config.section,
                status=OK,
                message=f"TCP port {host}:{port} is reachable",
                details={
                    "host": host,
                    "port": port,
                    "timeout_seconds": timeout_seconds,
                    "elapsed_ms": elapsed_ms,
                },
            )
    except OSError as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return CheckResult(
            name=config.section,
            status=CRIT,
            message=f"TCP port {host}:{port} is not reachable",
            details={
                "host": host,
                "port": port,
                "timeout_seconds": timeout_seconds,
                "elapsed_ms": elapsed_ms,
                "error": str(exc),
                "problem_code": "port_unreachable",
                "problem_codes": ["port_unreachable"],
            },
        )
