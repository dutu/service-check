from __future__ import annotations

import logging
import inspect
import time
from datetime import datetime, timezone
from typing import Any, Callable

from service_check.checks import get_check
from service_check.kuma import push_kuma
from service_check.models import CRIT, OK, UNKNOWN, WARN, CheckConfig, CheckDefaults, CheckResult, GlobalConfig, LoadedConfig
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
    show_results: bool = False,
) -> int:
    started = time.monotonic()
    LOGGER.debug(
        "run_start enabled_checks=%d check_section=%s run_all=%s dry_run=%s no_notify=%s state_file=%s",
        len(loaded.checks),
        check_section or "-",
        run_all,
        dry_run,
        no_notify,
        loaded.global_config.state_file,
    )
    store = StateStore(
        loaded.global_config.state_file,
        loaded.global_config.lock_file,
        lock_timeout_seconds=loaded.global_config.max_lock_hold_minutes * 60,
    )
    worst_status = OK
    if dry_run:
        state = store.load()
        selected = select_checks(
            loaded.checks,
            checks_state=state.setdefault("checks", {}),
            defaults=loaded.defaults,
            check_section=check_section,
            run_all=run_all,
        )
        if not selected:
            return handle_no_checks(check_section)
        LOGGER.debug("checks_selected count=%d sections=%s", len(selected), _format_sections(selected))
        worst_status = process_selected_checks(
            selected=selected,
            checks_state=state.setdefault("checks", {}),
            global_config=loaded.global_config,
            defaults=loaded.defaults,
            dry_run=dry_run,
            no_notify=no_notify,
            max_run_seconds=loaded.global_config.max_run_seconds,
            save_state=None,
            show_results=show_results,
        )
    else:
        with store.locked(save=False) as state:
            selected = select_checks(
                loaded.checks,
                checks_state=state.setdefault("checks", {}),
                defaults=loaded.defaults,
                check_section=check_section,
                run_all=run_all,
            )
            if not selected:
                return handle_no_checks(check_section)
            LOGGER.debug("checks_selected count=%d sections=%s", len(selected), _format_sections(selected))
            worst_status = process_selected_checks(
                selected=selected,
                checks_state=state.setdefault("checks", {}),
                global_config=loaded.global_config,
                defaults=loaded.defaults,
                dry_run=dry_run,
                no_notify=no_notify,
                max_run_seconds=loaded.global_config.max_run_seconds,
                save_state=lambda: store.save(state),
                show_results=show_results,
            )

    exit_code = 1 if worst_status in {CRIT, UNKNOWN} else 0
    LOGGER.debug(
        "run_end worst_status=%s exit_code=%d duration_ms=%d",
        worst_status,
        exit_code,
        _elapsed_ms(started),
    )
    return exit_code


def process_selected_checks(
    selected: list[CheckConfig],
    global_config: GlobalConfig,
    defaults: CheckDefaults,
    checks_state: dict[str, Any],
    dry_run: bool,
    no_notify: bool,
    max_run_seconds: float,
    save_state: Callable[[], None] | None,
    show_results: bool = False,
) -> str:
    run_started = time.monotonic()
    worst_status = OK
    for index, check_config in enumerate(selected):
        if index > 0 and _budget_exhausted(run_started, max_run_seconds):
            LOGGER.warning(
                "run_budget_exhausted max_run_seconds=%s processed=%d remaining=%d",
                _format_number(max_run_seconds),
                index,
                len(selected) - index,
            )
            break
        started = time.monotonic()
        previous = checks_state.get(check_config.section, {})
        check_state = previous.get("check_state") if isinstance(previous, dict) else None
        result = run_check_with_retries(
            check_config,
            defaults,
            check_state if isinstance(check_state, dict) else {},
        )
        duration_ms = _elapsed_ms(started)
        if result.status in {CRIT, UNKNOWN}:
            worst_status = result.status
        process_result(
            global_config=global_config,
            defaults=defaults,
            check_config=check_config,
            result=result,
            checks_state=checks_state,
            dry_run=dry_run,
            no_notify=no_notify,
            duration_ms=duration_ms,
        )
        if save_state:
            save_state()
        if show_results:
            print(f"{check_config.section}: {result.status} - {result.message}")
    return worst_status


def select_checks(
    checks: list[CheckConfig],
    checks_state: dict[str, Any],
    defaults: CheckDefaults,
    check_section: str | None,
    run_all: bool,
) -> list[CheckConfig]:
    if check_section:
        return [check for check in checks if check.section == check_section]
    if run_all:
        return sorted(checks, key=lambda check: _last_run_sort_key(check, checks_state))
    now = datetime.now(timezone.utc)
    due_checks = [
        check
        for check in checks
        if is_due(check, checks_state.get(check.section, {}), defaults, now)
    ]
    return sorted(due_checks, key=lambda check: _last_run_sort_key(check, checks_state))


def handle_no_checks(check_section: str | None) -> int:
    if check_section:
        LOGGER.error("check section not found or disabled: %s", check_section)
        return 2
    LOGGER.debug("no checks due")
    return 0


def is_due(
    check_config: CheckConfig,
    previous: dict[str, Any],
    defaults: CheckDefaults,
    now: datetime,
) -> bool:
    last_run_at = previous.get("last_run_at")
    if not last_run_at:
        return True

    interval_minutes = check_config.get_float("interval_minutes", defaults.interval_minutes)
    if interval_minutes <= 0:
        return True

    try:
        previous_run = datetime.fromisoformat(str(last_run_at).replace("Z", "+00:00"))
    except ValueError:
        return True

    interval_seconds = interval_minutes * 60
    return _interval_slot(now, interval_seconds) > _interval_slot(previous_run, interval_seconds)


def run_check_with_retries(
    check_config: CheckConfig,
    defaults: CheckDefaults,
    check_state: dict[str, Any] | None = None,
) -> CheckResult:
    retries = check_config.get_int("retries", defaults.retries)
    retry_delay_seconds = check_config.get_float("retry_delay_seconds", defaults.retry_delay_seconds)
    attempts = retries + 1
    last_result: CheckResult | None = None

    for attempt in range(1, attempts + 1):
        last_result = run_one_check(check_config, defaults, check_state or {})
        if last_result.status not in {CRIT, UNKNOWN}:
            if attempt > 1:
                LOGGER.info(
                    "check_retry_recovered section=%s attempt=%d attempts=%d status=%s",
                    check_config.section,
                    attempt,
                    attempts,
                    last_result.status,
                )
            return last_result
        if attempt < attempts:
            LOGGER.info(
                "check_retry section=%s attempt=%d attempts=%d status=%s delay_seconds=%s",
                check_config.section,
                attempt,
                attempts,
                last_result.status,
                _format_number(retry_delay_seconds),
            )
            time.sleep(retry_delay_seconds)

    assert last_result is not None
    return last_result


def run_one_check(
    check_config: CheckConfig,
    defaults: CheckDefaults,
    check_state: dict[str, Any] | None = None,
) -> CheckResult:
    try:
        check_fn = get_check(check_config.check)
        merged_options = dict(check_config.options)
        merged_options.setdefault("timeout_seconds", str(defaults.timeout_seconds))
        merged_config = CheckConfig(
            section=check_config.section,
            check=check_config.check,
            options=merged_options,
        )
        if _accepts_check_state(check_fn):
            return check_fn(merged_config, check_state or {})
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
    defaults: CheckDefaults,
    check_config: CheckConfig,
    result: CheckResult,
    checks_state: dict[str, Any],
    dry_run: bool,
    no_notify: bool,
    duration_ms: int,
) -> None:
    now = _utc_now()
    previous = checks_state.get(check_config.section, {})
    notify_on_warn = check_config.get_bool("notify_on_warn", False)
    was_problem = previous.get("last_problem", False)
    is_problem = result.status in {CRIT, UNKNOWN} or (
        result.status == WARN and (notify_on_warn or was_problem)
    )
    previous_result = previous.get("last_result")
    previous_status = previous_result.get("status") if isinstance(previous_result, dict) else None
    previous_details = previous_result.get("details") if isinstance(previous_result, dict) else None
    previous_problem_code = previous_details.get("problem_code") if isinstance(previous_details, dict) else None
    problem_code = get_problem_code(result)
    consecutive = int(previous.get("consecutive_failures", 0))

    if is_problem:
        consecutive += 1
    else:
        consecutive = 0

    context = build_message_context(global_config, defaults, check_config, result, consecutive, was_problem)
    message = render_result_message(check_config, result, context)

    should_notify = False
    notification_reason = "not_needed"
    if is_problem:
        fail_after = check_config.get_int("fail_after", defaults.fail_after)
        notify_repeat_after_seconds = int(
            check_config.get_float("notify_repeat_after_minutes", defaults.notify_repeat_after_minutes)
            * 60
        )
        last_notification_at = previous.get("last_notification_at")
        problem_changed = was_problem and (
            (previous_status is not None and previous_status != result.status)
            or (
                problem_code is not None
                and previous_problem_code is not None
                and previous_problem_code != problem_code
            )
        )
        should_notify = consecutive >= fail_after and (
            not was_problem
            or consecutive == fail_after
            or problem_changed
            or _seconds_since(last_notification_at, now) >= notify_repeat_after_seconds
        )
        if should_notify:
            notification_reason = "problem_changed" if problem_changed else "problem"
        elif consecutive < fail_after:
            notification_reason = "below_fail_after"
        else:
            notification_reason = "repeat_interval_not_elapsed"
    elif was_problem and check_config.get_bool("notify_on_recovery", defaults.notify_on_recovery):
        should_notify = True
        notification_reason = "recovery"
    elif was_problem:
        notification_reason = "recovery_disabled"
    elif result.status == OK and check_config.get_bool("notify_on_first_success", defaults.notify_on_first_success):
        should_notify = not previous.get("last_success_notification_at")
        notification_reason = "first_success" if should_notify else "first_success_already_sent"

    notification_error = None
    notify_cmd = render_notify_cmd(check_config, defaults, context) if should_notify and not no_notify else None
    notification_was_sent = bool(notify_cmd)
    notification_action = "none"
    if should_notify and no_notify:
        notification_action = "suppressed_no_notify"
    elif notify_cmd and dry_run:
        notification_action = "dry_run"
    elif notify_cmd:
        notification_action = "attempted"
    if notify_cmd:
        notification_error = send_notification(notify_cmd, message, dry_run)
        if notification_error:
            notification_was_sent = False
            notification_action = "failed"
            LOGGER.warning("notification failed for %s: %s", check_config.section, notification_error)
        elif not dry_run:
            notification_action = "sent"

    kuma_configured = bool(check_config.get("kuma_push_url"))
    kuma_action = "none"
    if kuma_configured:
        kuma_action = "dry_run" if dry_run else "attempted"
    kuma_error = push_kuma(
        check_config.get("kuma_push_url"),
        result.status,
        message,
        timeout=check_config.get_float("timeout_seconds", defaults.timeout_seconds),
        dry_run=dry_run,
    )
    if kuma_error:
        kuma_action = "failed"
        LOGGER.warning("kuma push failed for %s: %s", check_config.section, kuma_error)
    elif kuma_configured and not dry_run:
        kuma_action = "sent"

    LOGGER.debug(
        "check_result section=%s check=%s status=%s duration_ms=%d consecutive_failures=%d was_problem=%s "
        "is_problem=%s problem_code=%s notification=%s notification_reason=%s kuma=%s message=%r",
        check_config.section,
        check_config.check,
        result.status,
        duration_ms,
        consecutive,
        was_problem,
        is_problem,
        problem_code or "-",
        notification_action,
        notification_reason,
        kuma_action,
        _truncate(message),
    )

    new_check_state = {
        "last_result": serialize_check_result(result),
        "last_problem": is_problem,
        "consecutive_failures": consecutive,
        "last_run_at": now,
        "last_seen_at": now,
        "last_rendered_message": message,
        "last_notification_at": now if notification_was_sent else previous.get("last_notification_at"),
        "last_success_notification_at": (
            now
            if result.status == OK and notification_was_sent
            else previous.get("last_success_notification_at")
        ),
        "check_state": result.state,
    }
    checks_state[check_config.section] = new_check_state
    log_check_state_change(check_config, previous, new_check_state)


def serialize_check_result(result: CheckResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "status": result.status,
        "message": result.message,
        "details": result.details,
    }


def log_check_state_change(
    check_config: CheckConfig,
    previous: dict[str, Any],
    current: dict[str, Any],
) -> None:
    previous_result = previous.get("last_result")
    current_result = current.get("last_result")
    previous_status = _state_result_status(previous_result)
    current_status = _state_result_status(current_result)
    previous_problem_code = _state_result_problem_code(previous_result)
    current_problem_code = _state_result_problem_code(current_result)
    previous_run_at = previous.get("last_run_at")
    current_run_at = current.get("last_run_at")
    semantic_changes = _semantic_state_changes(
        previous_status,
        current_status,
        bool(previous.get("last_problem", False)),
        bool(current.get("last_problem", False)),
        previous_problem_code,
        current_problem_code,
    )
    if not semantic_changes:
        return

    LOGGER.info(
        "check_state_change section=%s check=%s changed=%s status=%s->%s problem=%s->%s "
        "problem_code=%s->%s consecutive_failures=%s->%s last_run_at=%s->%s "
        "previous_run_at=%s current_run_at=%s seconds_since_previous_run=%s "
        "last_notification_at=%s->%s last_success_notification_at=%s->%s check_state_changed=%s",
        check_config.section,
        check_config.check,
        ",".join(semantic_changes),
        previous_status or "-",
        current_status or "-",
        previous.get("last_problem", "-"),
        current.get("last_problem", "-"),
        previous_problem_code or "-",
        current_problem_code or "-",
        previous.get("consecutive_failures", "-"),
        current.get("consecutive_failures", "-"),
        previous_run_at or "-",
        current_run_at or "-",
        previous_run_at or "-",
        current_run_at or "-",
        _seconds_between_iso(previous_run_at, current_run_at),
        previous.get("last_notification_at", "-"),
        current.get("last_notification_at", "-"),
        previous.get("last_success_notification_at", "-"),
        current.get("last_success_notification_at", "-"),
        previous.get("check_state") != current.get("check_state"),
    )


def _semantic_state_changes(
    previous_status: str | None,
    current_status: str | None,
    previous_problem: bool,
    current_problem: bool,
    previous_problem_code: str | None,
    current_problem_code: str | None,
) -> list[str]:
    changes = []
    if previous_status != current_status:
        changes.append("status")
    if previous_problem != current_problem:
        changes.append("problem")
    if previous_problem_code != current_problem_code:
        changes.append("problem_code")
    return changes


def _state_result_status(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    return status if isinstance(status, str) else None


def _state_result_problem_code(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    details = value.get("details")
    if not isinstance(details, dict):
        return None
    problem_code = details.get("problem_code")
    return problem_code if isinstance(problem_code, str) and problem_code else None


def _seconds_between_iso(previous_iso: Any, current_iso: Any) -> str:
    if not isinstance(previous_iso, str) or not isinstance(current_iso, str):
        return "-"
    try:
        previous = datetime.fromisoformat(previous_iso.replace("Z", "+00:00"))
        current = datetime.fromisoformat(current_iso.replace("Z", "+00:00"))
    except ValueError:
        return "-"
    return str(int((current - previous).total_seconds()))


def _accepts_check_state(check_fn: Callable[..., CheckResult]) -> bool:
    try:
        parameters = inspect.signature(check_fn).parameters
    except (TypeError, ValueError):
        return False
    return len(parameters) >= 2


def render_result_message(check_config: CheckConfig, result: CheckResult, context: dict[str, Any]) -> str:
    if result.status == OK:
        template = check_config.get("success_message")
        if template:
            return render_template(template, context)
        return result.message
    problem_code = get_problem_code(result)
    if problem_code:
        template = check_config.get(f"failure_message.{problem_code}")
        if template:
            return render_template(template, context)
    template = check_config.get("failure_message")
    if template:
        return render_template(template, context)
    return result.message


def get_problem_code(result: CheckResult) -> str | None:
    value = result.details.get("problem_code")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def build_message_context(
    global_config: GlobalConfig,
    defaults: CheckDefaults,
    check_config: CheckConfig,
    result: CheckResult,
    failure_count: int,
    was_problem: bool = False,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "hostname": global_config.hostname,
        "section": check_config.section,
        "check": check_config.check,
        "interval_minutes": check_config.get_float("interval_minutes", defaults.interval_minutes),
        "name": result.name,
        "status": result.status,
        "notify_level": get_notify_level(result.status, is_recovery=result.status == OK and was_problem),
        "message": result.message,
        "failure_count": failure_count,
    }
    context.update(check_config.options)
    context.update(result.details)
    return context


def render_notify_cmd(
    check_config: CheckConfig,
    defaults: CheckDefaults,
    context: dict[str, Any],
) -> str | None:
    notify_cmd = check_config.get("notify_cmd", defaults.notify_cmd)
    if not notify_cmd:
        return None
    return render_template(notify_cmd, context)


def get_notify_level(status: str, is_recovery: bool = False) -> str:
    if is_recovery:
        return "notice"
    return {
        OK: "info",
        WARN: "warning",
        CRIT: "crit",
        UNKNOWN: "err",
    }.get(status, "notice")


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


def _interval_slot(moment: datetime, interval_seconds: float) -> int:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() // interval_seconds)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _format_sections(checks: list[CheckConfig]) -> str:
    return ",".join(check.section for check in checks)


def _budget_exhausted(started: float, max_run_seconds: float) -> bool:
    return max_run_seconds > 0 and (time.monotonic() - started) >= max_run_seconds


def _last_run_sort_key(check_config: CheckConfig, checks_state: dict[str, Any]) -> tuple[datetime, str]:
    previous = checks_state.get(check_config.section, {})
    last_run_at = previous.get("last_run_at") if isinstance(previous, dict) else None
    if not last_run_at:
        return datetime.min.replace(tzinfo=timezone.utc), check_config.section
    try:
        parsed = datetime.fromisoformat(str(last_run_at).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc), check_config.section
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed, check_config.section


def _truncate(value: str, limit: int = 240) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return str(value)
