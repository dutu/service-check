from __future__ import annotations

from service_check.models import OK, CheckConfig, CheckResult

CHECK_METADATA = {
    "description": "Reports OK when the service-check runner executes on this machine.",
    "statuses": {
        OK: "The service-check runner reached this check.",
    },
    "details": {
        "heartbeat": "Always true when this check runs.",
    },
}


def run(config: CheckConfig) -> CheckResult:
    return CheckResult(
        name=config.section,
        status=OK,
        message="service-check heartbeat OK",
        details={"heartbeat": True},
    )
