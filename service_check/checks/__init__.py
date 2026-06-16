from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from service_check.models import CheckConfig, CheckResult

CheckFunction = Callable[[CheckConfig], CheckResult]


def get_check(name: str) -> CheckFunction:
    module = _import_check_module(name)
    check_fn = getattr(module, "run", None)
    if check_fn is None:
        raise KeyError(f"check module '{name}' does not expose run(config)")
    return check_fn


def get_check_metadata(name: str) -> dict[str, Any]:
    module = _import_check_module(name)
    metadata = getattr(module, "CHECK_METADATA", None)
    if not isinstance(metadata, dict):
        raise KeyError(f"check module '{name}' does not expose CHECK_METADATA")
    return metadata


def _import_check_module(name: str) -> Any:
    try:
        return importlib.import_module(f"service_check.checks.{name}.check")
    except ModuleNotFoundError as exc:
        raise KeyError(f"unknown check module '{name}'") from exc
