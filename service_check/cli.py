from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from service_check import __version__
from service_check.config import DEFAULT_CONFIG_PATH, load_config, render_effective_config, validate_config
from service_check.models import CheckConfig, CheckDefaults, LoadedConfig
from service_check.runner import is_due, run
from service_check.state import StateStore

SCHEDULE_COLUMNS = ["SECTION", "CHECK", "INTERVAL_MIN", "LAST_RUN_AT", "NEXT_DUE_AT", "LAST_STATUS"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local service health checks.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="INI config path")
    parser.add_argument("--config-dir", help="Optional INI drop-in directory. Defaults to <config>.d")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--all", action="store_true", help="Run all enabled checks, ignoring interval_minutes")
    selection.add_argument("--check", dest="check_section", help="Run one check section regardless of interval")
    selection.add_argument(
        "--results-for",
        metavar="SECTION|all",
        help="Show results for one enabled section, or all enabled sections with 'all'",
    )
    parser.add_argument("--list-scheduled", action="store_true", help="List enabled checks and their schedule state")
    parser.add_argument("--validate-config", action="store_true", help="Validate config and exit without running checks")
    parser.add_argument("--print-config", action="store_true", help="Print effective enabled config and exit")
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
        if args.validate_config or args.print_config:
            issues = validate_config(args.config, args.config_dir)
            if issues:
                for issue in issues:
                    logging.error("%s", issue)
                return 2
            if args.validate_config and not args.print_config:
                logging.info("configuration OK")
                return 0

        loaded = load_config(args.config, args.config_dir)
        if args.print_config:
            print(render_effective_config(loaded))
            return 0

        check_section, run_all = resolve_selection(args.check_section, args.all, args.results_for)
        if args.list_scheduled:
            return list_scheduled_checks(loaded, check_section)
        return run(
            loaded,
            check_section=check_section,
            run_all=run_all,
            dry_run=args.dry_run,
            no_notify=args.no_notify,
        )
    except Exception as exc:  # noqa: BLE001 - CLI should convert failures to exit code.
        logging.error("%s", exc)
        return 2


def resolve_selection(check_section: str | None, run_all: bool, results_for: str | None) -> tuple[str | None, bool]:
    if results_for is None:
        return check_section, run_all
    if results_for == "all":
        return None, True
    return results_for, False


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

    now = datetime.now(timezone.utc)
    rows = []
    for check_config in checks:
        previous = checks_state.get(check_config.section, {})
        rows.append(format_scheduled_check(loaded.defaults, check_config, previous, now))
    print(format_table(SCHEDULE_COLUMNS, rows))
    return 0


def format_scheduled_check(
    defaults: CheckDefaults,
    check_config: CheckConfig,
    previous: dict[str, Any],
    now: datetime,
) -> str:
    interval_minutes = check_config.get_float("interval_minutes", defaults.interval_minutes)
    last_run_at = str(previous.get("last_run_at") or "-")
    is_check_due = is_due(check_config, previous, defaults, now)
    next_due_at = "due now" if is_check_due else compute_next_due_at(last_run_at, interval_minutes)
    last_result = previous.get("last_result")
    last_status = str(last_result.get("status") if isinstance(last_result, dict) else "-")
    return [
        check_config.section,
        check_config.check,
        _format_number(interval_minutes),
        format_local_time(last_run_at),
        next_due_at,
        last_status,
    ]


def format_table(columns: list[str], rows: list[list[str]]) -> str:
    widths = [
        max(len(row[index]) for row in [columns, *rows])
        for index in range(len(columns))
    ]
    lines = [_format_table_row(columns, widths)]
    lines.extend(_format_table_row(row, widths) for row in rows)
    return "\n".join(lines)


def _format_table_row(row: list[str], widths: list[int]) -> str:
    return "  ".join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip()


def compute_next_due_at(last_run_at: str, interval_minutes: float) -> str:
    if last_run_at == "-" or interval_minutes <= 0:
        return "-"
    try:
        previous_run = datetime.fromisoformat(last_run_at.replace("Z", "+00:00"))
    except ValueError:
        return "-"
    return format_local_time((previous_run + timedelta(minutes=interval_minutes)).isoformat())


def format_local_time(value: str) -> str:
    if value == "-":
        return "-"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.astimezone().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return str(value)


if __name__ == "__main__":
    sys.exit(main())
