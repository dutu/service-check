from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from service_check.checks import get_check
from service_check.kuma import push_kuma
from service_check.models import CRIT, OK, UNKNOWN, CheckConfig, CheckResult, GlobalConfig, LoadedConfig
from service_check.notify import send_notification
from service_check.state import StateStore
from service_check.templates import render_template

LOGGER = logging.getLogger(__name__)


def run(
    loaded: LoadedConfig,
    check_section: str | None = None,
    run_all: bool = False,
    dry_run: bool = False,
    no_notify: bool = False,
) -> int:
    store = StateStore(loaded.global_config.state_file, loaded.global_config.lock_file)
    worst_status = OK
    if dry_run:
        state = store.load()
        selected = select_checks(
            loaded.checks,
            checks_state=state.setdefault("checks", {}),
            global_config=loaded.global_config,
            check_section=check_section,
            run_all=run_all,
        )
        if not selected:
            return handle_no_checks(check_section)
        worst_status = process_selected_checks(
            selected=selected,
            checks_state=state.setdefault("checks", {}),
            global_config=loaded.global_config,
            dry_run=dry_run,
            no_notify=no_notify,
        )
    else:
        with store.locked() as state:
            selected = select_checks(
                loaded.checks,
                checks_state=state.setdefault("checks", {}),
                global_config=loaded.global_config,
                check_section=check_section,
                run_all=run_all,
            )
            if not selected:
                return handle_no_checks(check_section)
            worst_status = process_selected_checks(
                selected=selected,
                checks_state=state.setdefault("checks", {}),
                global_config=loaded.global_config,
                dry_run=dry_run,
                no_notify=no_notify,
            )

    return 1 if worst_status in {CRIT, UNKNOWN} else 0


def process_selected_checks(
    selected: list[CheckConfig],
    global_config: GlobalConfig,
    checks_state: dict[str, Any],
    dry_run: bool,
    no_notify: bool,
) -> str:
    worst_status = OK
    for check_config in selected:
        result = run_check_with_retries(check_config, global_config)
        if result.status in {CRIT, UNKNOWN}:
            worst_status = result.status
        process_result(
            global_config=global_config,
            check_config=check_config,
            result=result,
            checks_state=checks_state,
            dry_run=dry_run,
            no_notify=no_notify,
        )
        print(f"{check_config.section}: {result.status} - {result.message}")
    return worst_status


def select_checks(
    checks: list[CheckConfig],
    checks_state: dict[str, Any],
    global_config: GlobalConfig,
    check_section: str | None,
    run_all: bool,
) -> list[CheckConfig]:
    if check_section:
        return [check for check in checks if check.section == check_section]
    if run_all:
        return checks
    now = datetime.now(timezone.utc)
    return [check for check in checks if is_due(check, checks_state.get(check.section, {}), global_config, now)]


def handle_no_checks(check_section: str | None) -> int:
    if check_section:
        LOGGER.error("check section not found or disabled: %s", check_section)
        return 2
    LOGGER.info("no checks due")
    return 0


def is_due(
    check_config: CheckConfig,
    previous: dict[str, Any],
    global_config: GlobalConfig,
    now: datetime,
) -> bool:
    last_run_at = previous.get("last_run_at")
    if not last_run_at:
        return True

    interval_minutes = check_config.get_float("interval_minutes", global_config.default_interval_minutes)
    if interval_minutes <= 0:
        return True

    try:
        previous_run = datetime.fromisoformat(str(last_run_at).replace("Z", "+00:00"))
    except ValueError:
        return True

    return (now - previous_run).total_seconds() >= interval_minutes * 60


def run_check_with_retries(check_config: CheckConfig, global_config: GlobalConfig) -> CheckResult:
    retries = check_config.get_int("retries", global_config.default_retries)
    retry_delay = check_config.get_float("retry_delay", global_config.default_retry_delay)
    attempts = retries + 1
    last_result: CheckResult | None = None

    for attempt in range(1, attempts + 1):
        last_result = run_one_check(check_config, global_config)
        if last_result.status not in {CRIT, UNKNOWN}:
            return last_result
        if attempt < attempts:
            time.sleep(retry_delay)

    assert last_result is not None
    return last_result


def run_one_check(check_config: CheckConfig, global_config: GlobalConfig) -> CheckResult:
    try:
        check_fn = get_check(check_config.check)
        merged_options = dict(check_config.options)
        merged_options.setdefault("default_timeout", str(global_config.default_timeout))
        merged_config = CheckConfig(
            section=check_config.section,
            check=check_config.check,
            options=merged_options,
        )
        return check_fn(merged_config)
    except Exception as exc:  # noqa: BLE001 - runner must isolate bad checks.
        LOGGER.exception("check %s failed unexpectedly", check_config.section)
        return CheckResult(
            name=check_config.section,
            status=UNKNOWN,
            message=f"check failed unexpectedly: {exc}",
            details={"error": str(exc)},
        )


def process_result(
    global_config: GlobalConfig,
    check_config: CheckConfig,
    result: CheckResult,
    checks_state: dict[str, Any],
    dry_run: bool,
    no_notify: bool,
) -> None:
    now = _utc_now()
    previous = checks_state.get(check_config.section, {})
    notify_on_warn = check_config.get_bool("notify_on_warn", False)
    is_problem = result.status in {CRIT, UNKNOWN} or (result.status == "WARN" and notify_on_warn)
    was_problem = previous.get("last_problem", False)
    consecutive = int(previous.get("consecutive_failures", 0))

    if is_problem:
        consecutive += 1
    else:
        consecutive = 0

    context = build_message_context(global_config, check_config, result, consecutive)
    message = render_result_message(check_config, result, context)

    should_notify = False
    if is_problem:
        fail_after = check_config.get_int("fail_after", global_config.default_fail_after)
        repeat_after = check_config.get_int("repeat_after", global_config.default_repeat_after)
        last_notification_at = previous.get("last_notification_at")
        should_notify = consecutive >= fail_after and (
            not was_problem
            or consecutive == fail_after
            or _seconds_since(last_notification_at, now) >= repeat_after
        )
    elif was_problem and global_config.notify_on_recovery:
        should_notify = True
        message = f"Recovered: {message}"

    notification_error = None
    if should_notify and not no_notify:
        notify_cmd = check_config.get("notify_cmd", global_config.notify_cmd)
        notification_error = send_notification(notify_cmd, format_alert(global_config, check_config, result, message), dry_run)
        if notification_error:
            LOGGER.warning("notification failed for %s: %s", check_config.section, notification_error)

    kuma_error = push_kuma(
        check_config.get("kuma_push_url"),
        result.status,
        message,
        timeout=check_config.get_float("timeout", global_config.default_timeout),
        dry_run=dry_run,
    )
    if kuma_error:
        LOGGER.warning("kuma push failed for %s: %s", check_config.section, kuma_error)

    checks_state[check_config.section] = {
        "last_status": result.status,
        "last_problem": is_problem,
        "consecutive_failures": consecutive,
        "last_run_at": now,
        "last_seen_at": now,
        "last_message": result.message,
        "last_rendered_message": message,
        "last_notification_at": now if should_notify and not notification_error else previous.get("last_notification_at"),
        "details": result.details,
    }


def render_result_message(check_config: CheckConfig, result: CheckResult, context: dict[str, Any]) -> str:
    if result.status == OK:
        template = check_config.get("success_message")
        if template:
            return render_template(template, context)
        return result.message
    template = check_config.get("failure_message")
    if template:
        return render_template(template, context)
    return result.message


def build_message_context(
    global_config: GlobalConfig,
    check_config: CheckConfig,
    result: CheckResult,
    failure_count: int,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "hostname": global_config.hostname,
        "section": check_config.section,
        "check": check_config.check,
        "interval_minutes": check_config.get_float("interval_minutes", global_config.default_interval_minutes),
        "name": result.name,
        "status": result.status,
        "message": result.message,
        "failure_count": failure_count,
    }
    context.update(result.details)
    return context


def format_alert(global_config: GlobalConfig, check_config: CheckConfig, result: CheckResult, message: str) -> str:
    return f"[{global_config.hostname}] {check_config.section} {result.status}: {message}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _seconds_since(previous_iso: str | None, now_iso: str) -> int:
    if not previous_iso:
        return 10**9
    try:
        previous = datetime.fromisoformat(previous_iso.replace("Z", "+00:00"))
        now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    except ValueError:
        return 10**9
    return int((now - previous).total_seconds())
