from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


OK = "OK"
WARN = "WARN"
CRIT = "CRIT"
UNKNOWN = "UNKNOWN"

PROBLEM_STATUSES = {CRIT, UNKNOWN}


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckConfig:
    section: str
    check: str
    options: dict[str, str]

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.options.get(key, default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.options.get(key)
        if value is None:
            return default
        return value.strip().lower() in {"1", "yes", "true", "on"}

    def get_int(self, key: str, default: int) -> int:
        value = self.options.get(key)
        if value is None or value == "":
            return default
        return int(value)

    def get_float(self, key: str, default: float) -> float:
        value = self.options.get(key)
        if value is None or value == "":
            return default
        return float(value)


@dataclass(frozen=True)
class GlobalConfig:
    hostname: str
    state_file: str
    lock_file: str
    notify_cmd: str | None
    notify_on_recovery: bool


@dataclass(frozen=True)
class CheckDefaults:
    interval_seconds: float
    timeout_seconds: float
    retries: int
    retry_delay_seconds: float
    fail_after: int
    notify_repeat_after_minutes: float


@dataclass(frozen=True)
class LoadedConfig:
    global_config: GlobalConfig
    defaults: CheckDefaults
    checks: list[CheckConfig]
