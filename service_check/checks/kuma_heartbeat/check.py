from __future__ import annotations

import socket

from service_check.kuma import push_kuma
from service_check.models import CRIT, OK, UNKNOWN, CheckConfig, CheckResult
from service_check.templates import render_template

DEFAULT_PUSH_MESSAGE = "{hostname} service-check Kuma heartbeat OK"

CHECK_METADATA = {
    "description": "Pushes a heartbeat to Uptime Kuma and reports whether the push succeeded.",
    "statuses": {
        OK: "The Uptime Kuma push succeeded.",
        CRIT: "The Uptime Kuma push failed.",
        UNKNOWN: "Required config is missing.",
    },
    "details": {
        "hostname": "Machine hostname used by the push message.",
        "pushed_message": "Message sent to Uptime Kuma.",
        "error": "Push error text; present on failure.",
        "problem_code": "Primary machine-readable problem reason.",
        "problem_codes": "List of machine-readable problem reasons.",
    },
}


def run(config: CheckConfig) -> CheckResult:
    heartbeat_url = config.get("heartbeat_url")
    hostname = socket.gethostname()
    pushed_message = _render_heartbeat_message(config, hostname)

    if not heartbeat_url:
        return CheckResult(
            name=config.section,
            status=UNKNOWN,
            message="kuma_heartbeat check requires heartbeat_url",
            details={
                "hostname": hostname,
                "pushed_message": pushed_message,
                "problem_code": "missing_heartbeat_url",
                "problem_codes": ["missing_heartbeat_url"],
            },
        )

    timeout_seconds = config.get_float("timeout_seconds", 5.0)
    error = push_kuma(heartbeat_url, OK, pushed_message, timeout_seconds)
    if error:
        return CheckResult(
            name=config.section,
            status=CRIT,
            message=f"Uptime Kuma heartbeat push failed: {error}",
            details={
                "hostname": hostname,
                "pushed_message": pushed_message,
                "timeout_seconds": timeout_seconds,
                "error": error,
                "problem_code": "kuma_push_failed",
                "problem_codes": ["kuma_push_failed"],
            },
        )

    return CheckResult(
        name=config.section,
        status=OK,
        message="Uptime Kuma heartbeat push succeeded",
        details={
            "hostname": hostname,
            "pushed_message": pushed_message,
            "timeout_seconds": timeout_seconds,
        },
    )


def _render_heartbeat_message(config: CheckConfig, hostname: str) -> str:
    template = config.get("heartbeat_message") or config.get("success_message") or DEFAULT_PUSH_MESSAGE
    context = {
        "hostname": hostname,
        "section": config.section,
        "check": config.check,
    }
    context.update(config.options)
    return render_template(template, context)
