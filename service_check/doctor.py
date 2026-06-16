from __future__ import annotations

import os
import json
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import service_check
from service_check import __version__
from service_check.checks import get_check
from service_check.config import discover_config_files, load_config, read_config_parser, validate_config
from service_check.models import LoadedConfig


OK = "OK"
WARN = "WARN"
ERROR = "ERROR"
MIN_PYTHON = (3, 11)


@dataclass(frozen=True)
class DoctorResult:
    status: str
    message: str


def run_doctor(path: str, config_dir: str | None = None) -> list[DoctorResult]:
    results = [
        DoctorResult(OK, f"service-check version: {__version__}"),
        *_check_python_runtime(),
        *_check_command_path(),
        *_check_package_location(),
        *_check_config_sources(path, config_dir),
        *_check_config_files(path, config_dir),
        *_validate_config(path, config_dir),
    ]

    try:
        loaded = load_config(path, config_dir)
    except Exception as exc:  # noqa: BLE001 - doctor reports load failure and keeps prior findings.
        results.append(DoctorResult(ERROR, f"effective config could not be loaded: {exc}"))
        return results

    results.append(DoctorResult(OK, "effective config loads"))
    results.extend(_check_runtime_path("state directory", loaded.global_config.state_file))
    results.extend(_check_runtime_path("lock directory", loaded.global_config.lock_file))
    results.extend(_check_state_file(loaded.global_config.state_file))
    results.extend(_check_check_modules(loaded))
    results.extend(_check_notify_commands(loaded))
    results.extend(_check_kuma_urls(loaded))
    results.extend(_check_systemd_units())
    return results


def has_errors(results: list[DoctorResult]) -> bool:
    return any(result.status == ERROR for result in results)


def _check_config_files(path: str, config_dir: str | None) -> list[DoctorResult]:
    results = []
    config_path = Path(path)
    if config_path.is_file() and os.access(config_path, os.R_OK):
        results.append(DoctorResult(OK, f"config file readable: {config_path}"))
    elif config_path.exists():
        results.append(DoctorResult(ERROR, f"config file is not readable: {config_path}"))
    else:
        results.append(DoctorResult(ERROR, f"config file missing: {config_path}"))

    dropin_dir = Path(config_dir) if config_dir else Path(f"{path}.d")
    if dropin_dir.exists():
        if dropin_dir.is_dir() and os.access(dropin_dir, os.R_OK):
            dropin_count = len(discover_config_files(path, config_dir)) - 1
            results.append(DoctorResult(OK, f"config drop-in directory readable: {dropin_dir} ({dropin_count} files)"))
        elif dropin_dir.is_dir():
            results.append(DoctorResult(ERROR, f"config drop-in directory is not readable: {dropin_dir}"))
        else:
            results.append(DoctorResult(ERROR, f"config drop-in path is not a directory: {dropin_dir}"))
    elif config_dir:
        results.append(DoctorResult(WARN, f"configured drop-in directory missing: {dropin_dir}"))
    else:
        results.append(DoctorResult(OK, f"config drop-in directory not present: {dropin_dir}"))

    return results


def _check_config_sources(path: str, config_dir: str | None) -> list[DoctorResult]:
    try:
        _parser, read_files = read_config_parser(path, config_dir)
    except Exception as exc:  # noqa: BLE001 - doctor reports config read failures separately.
        return [DoctorResult(ERROR, f"config sources could not be read: {exc}")]
    return [DoctorResult(OK, f"config source loaded: {read_file}") for read_file in read_files]


def _check_python_runtime() -> list[DoctorResult]:
    version = sys.version_info
    version_text = f"{version.major}.{version.minor}.{version.micro}"
    minimum_text = ".".join(str(part) for part in MIN_PYTHON)
    results = [DoctorResult(OK, f"python executable: {sys.executable}")]
    if version >= MIN_PYTHON:
        results.append(DoctorResult(OK, f"python version: {version_text}"))
    else:
        results.append(DoctorResult(ERROR, f"python version {version_text} is below required {minimum_text}"))
    return results


def _check_command_path() -> list[DoctorResult]:
    command_path = shutil.which("service-check")
    if command_path:
        return [DoctorResult(OK, f"service-check command path: {command_path}")]
    return [DoctorResult(WARN, "service-check command not found on PATH")]


def _check_package_location() -> list[DoctorResult]:
    package_file = getattr(service_check, "__file__", None)
    if package_file:
        return [DoctorResult(OK, f"service_check package: {package_file}")]
    return [DoctorResult(WARN, "service_check package location unavailable")]


def _validate_config(path: str, config_dir: str | None) -> list[DoctorResult]:
    try:
        issues = validate_config(path, config_dir)
    except Exception as exc:  # noqa: BLE001 - doctor reports validation failure.
        return [DoctorResult(ERROR, f"config validation could not run: {exc}")]
    if not issues:
        return [DoctorResult(OK, "config validation passed")]
    return [DoctorResult(ERROR, issue) for issue in issues]


def _check_runtime_path(label: str, file_path: str) -> list[DoctorResult]:
    path = Path(file_path).expanduser()
    parent = path.parent
    if parent.exists():
        if not parent.is_dir():
            return [DoctorResult(ERROR, f"{label} is not a directory: {parent}")]
        if os.access(parent, os.W_OK):
            return [DoctorResult(OK, f"{label} writable: {parent}")]
        return [DoctorResult(ERROR, f"{label} is not writable: {parent}")]

    creatable_result = _check_parent_creatable(parent)
    if creatable_result:
        return [DoctorResult(OK, f"{label} can be created: {parent}")]
    return [DoctorResult(ERROR, f"{label} cannot be created: {parent}")]


def _check_parent_creatable(path: Path) -> bool:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current.is_dir() and os.access(current, os.W_OK)


def _check_state_file(state_file: str) -> list[DoctorResult]:
    path = Path(state_file).expanduser()
    if not path.exists():
        return [DoctorResult(OK, f"state file not present yet: {path}")]
    if not path.is_file():
        return [DoctorResult(ERROR, f"state file path is not a file: {path}")]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [DoctorResult(ERROR, f"state file is not valid JSON: {path}: {exc}")]
    if not isinstance(payload, dict):
        return [DoctorResult(ERROR, f"state file JSON root must be an object: {path}")]
    return [DoctorResult(OK, f"state file JSON readable: {path}")]


def _check_check_modules(loaded: LoadedConfig) -> list[DoctorResult]:
    results = []
    for check_config in loaded.checks:
        try:
            get_check(check_config.check)
        except KeyError as exc:
            results.append(DoctorResult(ERROR, f"[{check_config.section}] check module unavailable: {exc}"))
        else:
            results.append(DoctorResult(OK, f"[{check_config.section}] check module importable: {check_config.check}"))
    if not results:
        results.append(DoctorResult(WARN, "no enabled checks configured"))
    return results


def _check_notify_commands(loaded: LoadedConfig) -> list[DoctorResult]:
    results = []
    default_notify_cmd = loaded.defaults.notify_cmd
    if default_notify_cmd:
        results.append(_check_notify_command("default", default_notify_cmd))
    for check_config in loaded.checks:
        notify_cmd = check_config.get("notify_cmd")
        if notify_cmd:
            results.append(_check_notify_command(check_config.section, notify_cmd))
    if not results:
        results.append(DoctorResult(OK, "no notify_cmd configured"))
    return results


def _check_notify_command(section: str, notify_cmd: str) -> DoctorResult:
    try:
        parts = shlex.split(notify_cmd)
    except ValueError as exc:
        return DoctorResult(ERROR, f"[{section}] notify_cmd is not parseable: {exc}")
    if not parts:
        return DoctorResult(ERROR, f"[{section}] notify_cmd is empty")
    executable = parts[0]
    if Path(executable).is_absolute():
        if os.access(executable, os.X_OK):
            return DoctorResult(OK, f"[{section}] notify_cmd executable found: {executable}")
        return DoctorResult(ERROR, f"[{section}] notify_cmd executable not found or not executable: {executable}")
    resolved = shutil.which(executable)
    if resolved:
        return DoctorResult(OK, f"[{section}] notify_cmd executable found: {resolved}")
    return DoctorResult(ERROR, f"[{section}] notify_cmd executable not found on PATH: {executable}")


def _check_kuma_urls(loaded: LoadedConfig) -> list[DoctorResult]:
    results = []
    for check_config in loaded.checks:
        kuma_url = check_config.get("kuma_push_url")
        if not kuma_url:
            continue
        parsed = urlparse(kuma_url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            results.append(DoctorResult(OK, f"[{check_config.section}] kuma_push_url shape valid"))
        else:
            results.append(DoctorResult(ERROR, f"[{check_config.section}] kuma_push_url must be http(s): {kuma_url}"))
    if not results:
        results.append(DoctorResult(OK, "no kuma_push_url configured"))
    return results


def _check_systemd_units() -> list[DoctorResult]:
    if os.name != "posix":
        return [DoctorResult(WARN, "systemd units not checked: non-POSIX platform")]
    if not shutil.which("systemctl"):
        return [DoctorResult(WARN, "systemd units not checked: systemctl unavailable")]

    results = []
    for unit_name in ("service-check.service", "service-check.timer"):
        results.append(_check_systemd_unit_file(unit_name))
    results.append(_check_systemd_unit_state("service-check.timer", "is-enabled", "enabled"))
    results.append(_check_systemd_unit_state("service-check.timer", "is-active", "active"))
    return results


def _check_systemd_unit_file(unit_name: str) -> DoctorResult:
    try:
        completed = subprocess.run(
            ["systemctl", "list-unit-files", unit_name, "--no-pager", "--no-legend"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DoctorResult(WARN, f"systemd unit not checked: {unit_name}: {exc}")

    output = completed.stdout.strip()
    if completed.returncode == 0 and output:
        return DoctorResult(OK, f"systemd unit found: {output}")
    return DoctorResult(WARN, f"systemd unit not found: {unit_name}")


def _check_systemd_unit_state(unit_name: str, command: str, expected: str) -> DoctorResult:
    try:
        completed = subprocess.run(
            ["systemctl", command, unit_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DoctorResult(WARN, f"systemd {command} not checked: {unit_name}: {exc}")

    output = completed.stdout.strip() or completed.stderr.strip()
    if completed.returncode == 0:
        return DoctorResult(OK, f"systemd {unit_name} is {expected}")
    return DoctorResult(WARN, f"systemd {unit_name} is not {expected}: {output}")
