from __future__ import annotations

import configparser
import logging
import os
import socket
from pathlib import Path

from service_check.models import CheckConfig, GlobalConfig, LoadedConfig


DEFAULT_CONFIG_PATH = "/etc/service-check/service-check.ini"
LOGGER = logging.getLogger(__name__)


def load_config(path: str, config_dir: str | None = None) -> LoadedConfig:
    parser = configparser.ConfigParser(interpolation=None)
    config_files = discover_config_files(path, config_dir)
    read_files = parser.read(config_files)
    if not read_files:
        raise FileNotFoundError(f"config file not found: {path}")
    LOGGER.debug("loaded config files: %s", ", ".join(read_files))

    global_section = parser["global"] if parser.has_section("global") else {}
    hostname = _get(global_section, "hostname", socket.gethostname())
    state_file = _get(global_section, "state_file", "/var/lib/service-check/state.json")
    lock_file = _get(global_section, "lock_file", f"{state_file}.lock")

    global_config = GlobalConfig(
        hostname=hostname,
        state_file=state_file,
        lock_file=lock_file,
        notify_cmd=_get_optional(global_section, "notify_cmd"),
        default_interval_minutes=float(_get(global_section, "default_interval_minutes", "5")),
        default_timeout=float(_get(global_section, "default_timeout", "5")),
        default_retries=int(_get(global_section, "default_retries", "0")),
        default_retry_delay=float(_get(global_section, "default_retry_delay", "1")),
        default_fail_after=int(_get(global_section, "default_fail_after", "1")),
        default_notify_repeat_after_minutes=float(_get(global_section, "default_notify_repeat_after_minutes", "60")),
        notify_on_recovery=_get_bool(global_section, "notify_on_recovery", True),
    )

    checks: list[CheckConfig] = []
    for section in parser.sections():
        if section == "global":
            continue
        values = {key: value for key, value in parser[section].items()}
        if not _truthy(values.get("enabled", "0")):
            continue
        check_name = values.get("check")
        if not check_name:
            raise ValueError(f"[{section}] is enabled but has no check= value")
        checks.append(CheckConfig(section=section, check=check_name, options=values))

    return LoadedConfig(global_config=global_config, checks=checks)


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
