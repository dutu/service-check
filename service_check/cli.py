from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from service_check import __version__
from service_check.config import DEFAULT_CONFIG_PATH, load_config
from service_check.models import CheckConfig, GlobalConfig, LoadedConfig
from service_check.runner import is_due, run
from service_check.state import StateStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local service health checks.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="INI config path")
    parser.add_argument("--config-dir", help="Optional INI drop-in directory. Defaults to <config>.d")
    parser.add_argument("--all", action="store_true", help="Run all enabled checks, ignoring interval_minutes")
    parser.add_argument("--check", dest="check_section", help="Run one check section regardless of interval")
    parser.add_argument("--list-scheduled", action="store_true", help="List enabled checks and their due status")
    parser.add_argument("--dry-run", action="store_true", help="Do not send notifications or Kuma pushes")
    parser.add_argument("--no-notify", action="store_true", help="Do not send local notifications")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    try:
        loaded = load_config(args.config, args.config_dir)
        if args.list_scheduled:
            return list_scheduled_checks(loaded, args.check_section)
        return run(
            loaded,
            check_section=args.check_section,
            run_all=args.all,
            dry_run=args.dry_run,
            no_notify=args.no_notify,
        )
    except Exception as exc:  # noqa: BLE001 - CLI should convert failures to exit code.
        logging.error("%s", exc)
        return 2


def list_scheduled_checks(loaded: LoadedConfig, check_section: str | None = None) -> int:
    store = StateStore(loaded.global_config.state_file, loaded.global_config.lock_file)
    state = store.load()
    checks_state = state.setdefault("checks", {})
    checks = [check for check in loaded.checks if check.section == check_section] if check_section else loaded.checks
    if not checks:
        if check_section:
            logging.error("check section not found or disabled: %s", check_section)
            return 2
        else:
            logging.info("no enabled checks")
            return 0

    print("SECTION\tCHECK\tINTERVAL_MIN\tDUE\tLAST_RUN_AT\tNEXT_RUN_AT\tLAST_STATUS")
    now = datetime.now(timezone.utc)
    for check_config in checks:
        previous = checks_state.get(check_config.section, {})
        print(format_scheduled_check(loaded.global_config, check_config, previous, now))
    return 0


def format_scheduled_check(
    global_config: GlobalConfig,
    check_config: CheckConfig,
    previous: dict[str, Any],
    now: datetime,
) -> str:
    interval_minutes = check_config.get_float("interval_minutes", global_config.default_interval_minutes)
    last_run_at = str(previous.get("last_run_at") or "-")
    next_run_at = compute_next_run_at(last_run_at, interval_minutes)
    due = "yes" if is_due(check_config, previous, global_config, now) else "no"
    last_status = str(previous.get("last_status") or "-")
    return "\t".join(
        [
            check_config.section,
            check_config.check,
            _format_number(interval_minutes),
            due,
            last_run_at,
            next_run_at,
            last_status,
        ]
    )


def compute_next_run_at(last_run_at: str, interval_minutes: float) -> str:
    if last_run_at == "-" or interval_minutes <= 0:
        return "-"
    try:
        previous_run = datetime.fromisoformat(last_run_at.replace("Z", "+00:00"))
    except ValueError:
        return "-"
    return (previous_run + timedelta(minutes=interval_minutes)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return str(value)


if __name__ == "__main__":
    sys.exit(main())
