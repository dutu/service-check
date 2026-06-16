from __future__ import annotations

import socket
import time

from service_check.models import CRIT, OK, UNKNOWN, CheckConfig, CheckResult


def run(config: CheckConfig) -> CheckResult:
    host = config.get("host")
    port_value = config.get("port")
    timeout = config.get_float("timeout", 5.0)

    if not host:
        return CheckResult(
            name=config.section,
            status=UNKNOWN,
            message="tcp_port check requires host",
            details={},
        )

    try:
        port = int(port_value or "")
    except ValueError:
        return CheckResult(
            name=config.section,
            status=UNKNOWN,
            message="tcp_port check requires numeric port",
            details={"host": host, "port": port_value},
        )

    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return CheckResult(
                name=config.section,
                status=OK,
                message=f"TCP port {host}:{port} is reachable",
                details={
                    "host": host,
                    "port": port,
                    "timeout": timeout,
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
                "timeout": timeout,
                "elapsed_ms": elapsed_ms,
                "error": str(exc),
            },
        )
