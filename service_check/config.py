from __future__ import annotations

import configparser
import difflib
import logging
import os
import socket
from pathlib import Path

from service_check.models import CheckConfig, CheckDefaults, GlobalConfig, LoadedConfig


DEFAULT_CONFIG_PATH = "/etc/service-check/service-check.ini"
LOGGER = logging.getLogger(__name__)
GLOBAL_KEYS = {"hostname", "state_file", "lock_file"}
DEFAULT_KEYS = {
    "notify_cmd",
    "interval_minutes",
    "timeout_seconds",
    "retries",
    "retry_delay_seconds",
    "fail_after",
    "notify_repeat_after_minutes",
    "notify_on_recovery",
}
COMMON_CHECK_KEYS = {
    "enabled",
    "check",
    "interval_minutes",
    "timeout_seconds",
    "retries",
    "retry_delay_seconds",
    "fail_after",
    "notify_repeat_after_minutes",
    "notify_on_recovery",
    "notify_on_warn",
    "notify_on_success_once",
    "notify_cmd",
    "notify_topic",
    "kuma_push_url",
    "failure_message",
    "success_message",
}
CHECK_KEYS = {
    "tcp_port": {"host", "port"},
    "github_release_update": {
        "repository",
        "repo",
        "api_url",
        "expected_version",
        "current_version",
    },
}
REQUIRED_CHECK_KEYS = {
    "tcp_port": {"host", "port"},
    "github_release_update": set(),
}
BOOL_KEYS = {"enabled", "notify_on_recovery", "notify_on_warn", "notify_on_success_once"}
INT_KEYS = {"retries", "fail_after", "port"}
FLOAT_KEYS = {"interval_minutes", "timeout_seconds", "retry_delay_seconds", "notify_repeat_after_minutes"}


def load_config(path: str, config_dir: str | None = None) -> LoadedConfig:
    parser, read_files = read_config_parser(path, config_dir)
    LOGGER.debug("loaded config files: %s", ", ".join(read_files))

    global_section = parser["global"] if parser.has_section("global") else {}
    default_section = parser["default"] if parser.has_section("default") else {}
    hostname = _get(global_section, "hostname", socket.gethostname())
    state_file = _get(global_section, "state_file", "/var/lib/service-check/state.json")
    lock_file = _get(global_section, "lock_file", f"{state_file}.lock")

    global_config = GlobalConfig(
        hostname=hostname,
        state_file=state_file,
        lock_file=lock_file,
    )
    defaults = CheckDefaults(
        notify_cmd=_get_optional(default_section, "notify_cmd"),
        interval_minutes=float(_get(default_section, "interval_minutes", "5")),
        timeout_seconds=float(_get(default_section, "timeout_seconds", "5")),
        retries=int(_get(default_section, "retries", "0")),
        retry_delay_seconds=float(_get(default_section, "retry_delay_seconds", "1")),
        fail_after=int(_get(default_section, "fail_after", "1")),
        notify_repeat_after_minutes=float(_get(default_section, "notify_repeat_after_minutes", "60")),
        notify_on_recovery=_get_bool(default_section, "notify_on_recovery", True),
    )

    checks: list[CheckConfig] = []
    for section in parser.sections():
        if section in {"global", "default"}:
            continue
        values = {key: value for key, value in parser[section].items()}
        if not _truthy(values.get("enabled", "0")):
            continue
        check_name = values.get("check")
        if not check_name:
            raise ValueError(f"[{section}] is enabled but has no check= value")
        checks.append(CheckConfig(section=section, check=check_name, options=values))

    return LoadedConfig(global_config=global_config, defaults=defaults, checks=checks)


def read_config_parser(path: str, config_dir: str | None = None) -> tuple[configparser.ConfigParser, list[str]]:
    parser = configparser.ConfigParser(interpolation=None)
    config_files = discover_config_files(path, config_dir)
    read_files = parser.read(config_files)
    if not read_files:
        raise FileNotFoundError(f"config file not found: {path}")
    return parser, read_files


def validate_config(path: str, config_dir: str | None = None) -> list[str]:
    parser, _read_files = read_config_parser(path, config_dir)
    issues: list[str] = []

    for section, allowed_keys in (("global", GLOBAL_KEYS), ("default", DEFAULT_KEYS)):
        if parser.has_section(section):
            issues.extend(_unknown_key_issues(section, parser[section], allowed_keys))
            issues.extend(_typed_value_issues(section, parser[section]))

    for section in parser.sections():
        if section in {"global", "default"}:
            continue
        values = parser[section]
        check_name = values.get("check", "").strip()
        if not check_name:
            issues.append(f"[{section}] missing required key: check")
            continue
        if check_name not in CHECK_KEYS:
            issues.append(_unknown_check_issue(section, check_name))
            continue

        allowed_keys = COMMON_CHECK_KEYS | CHECK_KEYS[check_name]
        issues.extend(_unknown_key_issues(section, values, allowed_keys))
        issues.extend(_missing_key_issues(section, values, REQUIRED_CHECK_KEYS[check_name]))
        issues.extend(_typed_value_issues(section, values))

    return issues


def render_effective_config(loaded: LoadedConfig) -> str:
    lines = [
        "[global]",
        f"hostname={loaded.global_config.hostname}",
        f"state_file={loaded.global_config.state_file}",
        f"lock_file={loaded.global_config.lock_file}",
        "",
        "[default]",
        *_render_options(_defaults_as_options(loaded.defaults)),
    ]

    default_options = _defaults_as_options(loaded.defaults)
    for check_config in loaded.checks:
        effective_options = {
            **default_options,
            **check_config.options,
            "enabled": "1",
            "check": check_config.check,
        }
        lines.extend(["", f"[{check_config.section}]", *_render_options(effective_options)])

    return "\n".join(lines)


def discover_config_files(path: str, config_dir: str | None = None) -> list[str]:
    main_path = Path(path)
    files = [str(main_path)]

    dropin_dir = Path(config_dir) if config_dir else Path(f"{path}.d")
    if dropin_dir.is_dir():
        files.extend(str(child) for child in sorted(dropin_dir.glob("*.ini")) if child.is_file())

    return files


def ensure_parent_dir(path: str) -> None:
    parent = Path(path).expanduser().resolve().parent
    os.makedirs(parent, exist_ok=True)


def _get(section: configparser.SectionProxy | dict[str, str], key: str, default: str) -> str:
    value = section.get(key) if hasattr(section, "get") else None
    if value is None or value == "":
        return default
    return str(value)


def _get_optional(section: configparser.SectionProxy | dict[str, str], key: str) -> str | None:
    value = section.get(key) if hasattr(section, "get") else None
    if value is None or value.strip() == "":
        return None
    return str(value)


def _get_bool(section: configparser.SectionProxy | dict[str, str], key: str, default: bool) -> bool:
    value = section.get(key) if hasattr(section, "get") else None
    if value is None or value == "":
        return default
    return _truthy(value)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "yes", "true", "on"}


def _unknown_key_issues(
    section: str,
    values: configparser.SectionProxy,
    allowed_keys: set[str],
) -> list[str]:
    issues = []
    for key in values:
        if key not in allowed_keys:
            issues.append(_unknown_key_issue(section, key, allowed_keys))
    return issues


def _unknown_key_issue(section: str, key: str, allowed_keys: set[str]) -> str:
    suggestion = _suggestion(key, allowed_keys)
    suffix = f"; did you mean {suggestion}?" if suggestion else ""
    return f"[{section}] unknown key: {key}{suffix}"


def _unknown_check_issue(section: str, check_name: str) -> str:
    suggestion = _suggestion(check_name, set(CHECK_KEYS))
    suffix = f"; did you mean {suggestion}?" if suggestion else ""
    return f"[{section}] unknown check module: {check_name}{suffix}"


def _missing_key_issues(
    section: str,
    values: configparser.SectionProxy,
    required_keys: set[str],
) -> list[str]:
    return [
        f"[{section}] missing required key: {key}"
        for key in sorted(required_keys)
        if not values.get(key, "").strip()
    ]


def _typed_value_issues(section: str, values: configparser.SectionProxy) -> list[str]:
    issues = []
    for key in BOOL_KEYS & set(values):
        if values[key].strip().lower() not in {"1", "yes", "true", "on", "0", "no", "false", "off"}:
            issues.append(f"[{section}] key {key} must be boolean")
    for key in INT_KEYS & set(values):
        try:
            int(values[key])
        except ValueError:
            issues.append(f"[{section}] key {key} must be an integer")
    for key in FLOAT_KEYS & set(values):
        try:
            float(values[key])
        except ValueError:
            issues.append(f"[{section}] key {key} must be a number")
    return issues


def _suggestion(value: str, candidates: set[str]) -> str | None:
    matches = difflib.get_close_matches(value, sorted(candidates), n=1)
    return matches[0] if matches else None


def _defaults_as_options(defaults: CheckDefaults) -> dict[str, str]:
    options = {
        "interval_minutes": _format_number(defaults.interval_minutes),
        "timeout_seconds": _format_number(defaults.timeout_seconds),
        "retries": str(defaults.retries),
        "retry_delay_seconds": _format_number(defaults.retry_delay_seconds),
        "fail_after": str(defaults.fail_after),
        "notify_repeat_after_minutes": _format_number(defaults.notify_repeat_after_minutes),
        "notify_on_recovery": _format_bool(defaults.notify_on_recovery),
    }
    if defaults.notify_cmd:
        options["notify_cmd"] = defaults.notify_cmd
    return options


def _render_options(options: dict[str, str]) -> list[str]:
    return [f"{key}={value}" for key, value in options.items()]


def _format_bool(value: bool) -> str:
    return "1" if value else "0"


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return str(value)
