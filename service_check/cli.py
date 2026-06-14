from __future__ import annotations

import argparse
import logging
import sys

from service_check import __version__
from service_check.config import DEFAULT_CONFIG_PATH, load_config
from service_check.runner import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local service health checks.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="INI config path")
    parser.add_argument("--config-dir", help="Optional INI drop-in directory. Defaults to <config>.d")
    parser.add_argument("--all", action="store_true", help="Run all enabled checks, ignoring interval_minutes")
    parser.add_argument("--check", dest="check_section", help="Run one check section regardless of interval")
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


if __name__ == "__main__":
    sys.exit(main())
