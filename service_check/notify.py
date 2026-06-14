from __future__ import annotations

import shlex
import subprocess


def send_notification(notify_cmd: str | None, message: str, dry_run: bool = False) -> str | None:
    if not notify_cmd:
        return None
    if dry_run:
        return None

    args = shlex.split(notify_cmd)
    if not args:
        return None

    completed = subprocess.run(
        [*args, message],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        return stderr or stdout or f"notification command failed with exit {completed.returncode}"
    return None

