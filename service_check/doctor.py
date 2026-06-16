from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from service_check.config import discover_config_files, load_config, validate_config


OK = "OK"
WARN = "WARN"
ERROR = "ERROR"


@dataclass(frozen=True)
class DoctorResult:
    status: str
    message: str


def run_doctor(path: str, config_dir: str | None = None) -> list[DoctorResult]:
    results = [
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
    results.extend(_check_systemd_unit())
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


def _check_systemd_unit() -> list[DoctorResult]:
    if os.name != "posix":
        return [DoctorResult(WARN, "systemd unit not checked: non-POSIX platform")]
    if not shutil.which("systemctl"):
        return [DoctorResult(WARN, "systemd unit not checked: systemctl unavailable")]

    try:
        completed = subprocess.run(
            ["systemctl", "list-unit-files", "service-check.service", "--no-pager", "--no-legend"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [DoctorResult(WARN, f"systemd unit not checked: {exc}")]

    output = completed.stdout.strip()
    if completed.returncode == 0 and output:
        return [DoctorResult(OK, f"systemd unit found: {output}")]
    return [DoctorResult(WARN, "systemd unit not found: service-check.service")]
