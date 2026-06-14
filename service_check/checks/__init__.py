from __future__ import annotations

import importlib
from collections.abc import Callable

from service_check.models import CheckConfig, CheckResult

CheckFunction = Callable[[CheckConfig], CheckResult]


def get_check(name: str) -> CheckFunction:
    try:
        module = importlib.import_module(f"service_check.checks.{name}.check")
    except ModuleNotFoundError as exc:
        raise KeyError(f"unknown check module '{name}'") from exc

    check_fn = getattr(module, "run", None)
    if check_fn is None:
        raise KeyError(f"check module '{name}' does not expose run(config)")
    return check_fn

