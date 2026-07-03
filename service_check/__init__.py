"""Local watchdog runner for small self-hosted services."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tomllib


def _read_version() -> str:
    try:
        return version("service-check")
    except PackageNotFoundError:
        pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        try:
            with pyproject_path.open("rb") as pyproject_file:
                return tomllib.load(pyproject_file)["project"]["version"]
        except (FileNotFoundError, KeyError, tomllib.TOMLDecodeError):
            return "0.0.0"


__version__ = _read_version()
