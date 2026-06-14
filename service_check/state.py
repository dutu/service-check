from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from service_check.config import ensure_parent_dir


class StateStore:
    def __init__(self, state_file: str, lock_file: str) -> None:
        self.state_file = state_file
        self.lock_file = lock_file

    @contextmanager
    def locked(self, save: bool = True) -> Iterator[dict[str, Any]]:
        ensure_parent_dir(self.state_file)
        ensure_parent_dir(self.lock_file)
        with open(self.lock_file, "a+", encoding="utf-8") as lock_handle:
            _lock_file(lock_handle)
            try:
                state = self.load()
                yield state
                if save:
                    self.save(state)
            finally:
                _unlock_file(lock_handle)

    def load(self) -> dict[str, Any]:
        path = Path(self.state_file)
        if not path.exists():
            return {"checks": {}}
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return {"checks": {}}
        data.setdefault("checks", {})
        return data

    def save(self, state: dict[str, Any]) -> None:
        ensure_parent_dir(self.state_file)
        tmp_path = f"{self.state_file}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, self.state_file)


def _lock_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
