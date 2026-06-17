from __future__ import annotations

import json
import re
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPBasicAuthHandler,
    HTTPDigestAuthHandler,
    HTTPPasswordMgrWithDefaultRealm,
    Request,
    build_opener,
    urlopen,
)

from service_check.models import CRIT, OK, UNKNOWN, WARN, CheckConfig, CheckResult

DEFAULT_CONFIG_FILE = "/etc/monero/monerod.conf"
DEFAULT_SERVICE_NAME = "monerod"
DEFAULT_P2P_PORT = 18080
DEFAULT_SYNC_STALL_SECONDS = 180
WILDCARD_HOSTS = {"0.0.0.0", "::", "*"}

CHECK_METADATA = {
    "description": "Checks monerod service state, configured TCP ports, RPC sync state, and peer thresholds.",
    "statuses": {
        OK: "monerod is active, configured ports are reachable, RPC reports synced, and peer thresholds pass.",
        WARN: "monerod is running but sync is pending or optional peer thresholds are degraded.",
        CRIT: "monerod is inactive, a configured port is closed, RPC is unhealthy, sync is stalled, or sync is not active while behind.",
        UNKNOWN: "Required local configuration could not be discovered, read, or parsed.",
    },
    "details": {
        "problem_code": "Primary machine-readable problem reason.",
        "problem_codes": "List of machine-readable problem reasons.",
        "service_name": "systemd service name checked with systemctl.",
        "config_file": "monerod config path used by the check.",
        "rpc_host": "Host used for unrestricted RPC checks.",
        "rpc_port": "Unrestricted RPC port from monerod config, when configured.",
        "restricted_rpc_host": "Host used for restricted RPC TCP checks, when configured.",
        "restricted_rpc_port": "Restricted RPC port from monerod config, when configured.",
        "p2p_host": "Host used for P2P TCP checks.",
        "p2p_port": "P2P port from monerod config or the default.",
        "height": "Current chain height reported by RPC.",
        "target_height": "Target chain height reported by RPC.",
        "synchronized": "RPC synchronized flag.",
        "busy_syncing": "RPC busy_syncing flag.",
        "offline": "RPC offline flag.",
        "outgoing_connections_count": "RPC outgoing peer count.",
        "incoming_connections_count": "RPC incoming peer count.",
        "sync_stalled_for_seconds": "Seconds since height last advanced while behind.",
        "error": "Combined error text; present on UNKNOWN/CRIT where relevant.",
    },
}


def run(config: CheckConfig, state: dict[str, Any] | None = None) -> CheckResult:
    previous_state = state or {}
    details: dict[str, Any] = {
        "service_name": config.get("service_name", DEFAULT_SERVICE_NAME) or DEFAULT_SERVICE_NAME,
        "config_file": "",
    }
    errors: list[tuple[str, str]] = []
    warnings: list[tuple[str, str]] = []
    new_state = dict(previous_state)

    service_name = details["service_name"]
    service_status = _systemctl_is_active(service_name, config.get_float("timeout_seconds", 5.0))
    if service_status.status == UNKNOWN:
        return _result(config, UNKNOWN, service_status.message, details, new_state, service_status.error, ["service_check_failed"])
    if service_status.status == CRIT:
        errors.append(("service_inactive", service_status.message))

    config_file = config.get("config_file") or _discover_config_file(service_name, config.get_float("timeout_seconds", 5.0))
    if not config_file:
        config_file = DEFAULT_CONFIG_FILE
    details["config_file"] = config_file

    try:
        monerod_config = _read_monerod_config(config_file)
    except OSError as exc:
        return _result(
            config,
            UNKNOWN,
            f"monerod config could not be read: {config_file}",
            details,
            new_state,
            str(exc),
            ["config_unreadable"],
        )

    port_checks = _build_port_checks(monerod_config)
    for port_check in port_checks:
        details[f"{port_check.name}_host"] = port_check.host
        details[f"{port_check.name}_port"] = port_check.port
        port_error = _check_tcp_port(port_check.host, port_check.port, config.get_float("timeout_seconds", 5.0))
        if port_error:
            errors.append((
                f"{port_check.name}_port_closed",
                f"{port_check.label} port {port_check.host}:{port_check.port} is not reachable: {port_error}",
            ))

    rpc_port = _get_int(monerod_config, "rpc-bind-port")
    if rpc_port:
        rpc_host = _connect_host(monerod_config.get("rpc-bind-ip", "127.0.0.1"))
        details["rpc_host"] = rpc_host
        details["rpc_port"] = rpc_port
        rpc_login = monerod_config.get("rpc-login")
        rpc_result = _fetch_get_info(
            host=rpc_host,
            port=rpc_port,
            rpc_login=rpc_login,
            timeout=config.get_float("timeout_seconds", 5.0),
        )
        if rpc_result.error:
            errors.append(("rpc_failed", f"monerod RPC get_info failed: {rpc_result.error}"))
        elif rpc_result.payload:
            sync_status, sync_message, sync_state, sync_code = _evaluate_sync(config, rpc_result.payload, previous_state)
            new_state.update(sync_state)
            details.update(_rpc_details(rpc_result.payload))
            if sync_status == CRIT:
                errors.append((sync_code, sync_message))
            elif sync_status == WARN:
                warnings.append((sync_code, sync_message))
            peer_status, peer_message, peer_code = _evaluate_peers(config, rpc_result.payload)
            if peer_status == CRIT:
                errors.append((peer_code, peer_message))
            elif peer_status == WARN:
                warnings.append((peer_code, peer_message))
    elif config.get_bool("require_rpc", False):
        errors.append(("rpc_not_configured", "unrestricted RPC is required but rpc-bind-port is not configured"))

    if errors:
        return _result(
            config,
            CRIT,
            "; ".join(message for _code, message in errors),
            details,
            new_state,
            "; ".join(message for _code, message in errors),
            [code for code, _message in errors],
        )
    if warnings:
        return _result(
            config,
            WARN,
            "; ".join(message for _code, message in warnings),
            details,
            new_state,
            problem_codes=[code for code, _message in warnings],
        )
    return _result(config, OK, "monerod is active, reachable, and synced", details, new_state)


class _ServiceStatus:
    def __init__(self, status: str, message: str, error: str = "") -> None:
        self.status = status
        self.message = message
        self.error = error


class _PortCheck:
    def __init__(self, name: str, label: str, host: str, port: int) -> None:
        self.name = name
        self.label = label
        self.host = host
        self.port = port


class _RpcResult:
    def __init__(self, payload: dict[str, Any] | None = None, error: str = "") -> None:
        self.payload = payload
        self.error = error


def _systemctl_is_active(service_name: str, timeout: float) -> _ServiceStatus:
    try:
        completed = subprocess.run(
            ["systemctl", "is-active", service_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return _ServiceStatus(UNKNOWN, "systemctl is unavailable", str(exc))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _ServiceStatus(UNKNOWN, f"could not check systemd service {service_name}", str(exc))

    status = completed.stdout.strip() or completed.stderr.strip() or f"exit {completed.returncode}"
    if completed.returncode == 0 and status == "active":
        return _ServiceStatus(OK, f"systemd service {service_name} is active")
    return _ServiceStatus(CRIT, f"systemd service {service_name} is not active: {status}", status)


def _discover_config_file(service_name: str, timeout: float) -> str | None:
    try:
        completed = subprocess.run(
            ["systemctl", "show", service_name, "--property=ExecStart", "--value"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return _parse_config_file_from_execstart(completed.stdout)


def _parse_config_file_from_execstart(exec_start: str) -> str | None:
    match = re.search(r"--config-file(?:=|\s+)(?P<path>[^\"'\s;]+)", exec_start)
    if match:
        return match.group("path")
    return None


def _read_monerod_config(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _build_port_checks(config: dict[str, str]) -> list[_PortCheck]:
    checks = [
        _PortCheck(
            name="p2p",
            label="P2P",
            host=_connect_host(config.get("p2p-bind-ip", "127.0.0.1")),
            port=_get_int(config, "p2p-bind-port") or DEFAULT_P2P_PORT,
        )
    ]
    rpc_port = _get_int(config, "rpc-bind-port")
    if rpc_port:
        checks.append(
            _PortCheck(
                name="rpc",
                label="RPC",
                host=_connect_host(config.get("rpc-bind-ip", "127.0.0.1")),
                port=rpc_port,
            )
        )
    restricted_rpc_port = _get_int(config, "rpc-restricted-bind-port")
    if restricted_rpc_port:
        checks.append(
            _PortCheck(
                name="restricted_rpc",
                label="restricted RPC",
                host=_connect_host(config.get("rpc-restricted-bind-ip", "127.0.0.1")),
                port=restricted_rpc_port,
            )
        )
    return checks


def _check_tcp_port(host: str, port: int, timeout: float) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return ""
    except OSError as exc:
        return str(exc)


def _fetch_get_info(host: str, port: int, rpc_login: str | None, timeout: float) -> _RpcResult:
    url = f"http://{_format_url_host(host)}:{port}/json_rpc"
    body = json.dumps({"jsonrpc": "2.0", "id": "0", "method": "get_info"}).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "service-check"}
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        opener = _auth_opener(url, rpc_login)
        response_context = opener.open(request, timeout=timeout) if opener else urlopen(request, timeout=timeout)
        with response_context as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
        return _RpcResult(error=str(exc))
    result = payload.get("result")
    if not isinstance(result, dict):
        return _RpcResult(error="RPC response did not include result object")
    return _RpcResult(payload=result)


def _auth_opener(url: str, rpc_login: str | None) -> Any | None:
    if not rpc_login:
        return None
    username, separator, password = rpc_login.partition(":")
    if not separator:
        return None
    password_manager = HTTPPasswordMgrWithDefaultRealm()
    password_manager.add_password(None, url, username, password)
    return build_opener(HTTPDigestAuthHandler(password_manager), HTTPBasicAuthHandler(password_manager))


def _evaluate_sync(
    config: CheckConfig,
    payload: dict[str, Any],
    previous_state: dict[str, Any],
) -> tuple[str, str, dict[str, Any], str]:
    now = _utc_now()
    height = _int_payload(payload, "height")
    target_height = _int_payload(payload, "target_height")
    synchronized = bool(payload.get("synchronized", False))
    busy_syncing = bool(payload.get("busy_syncing", False))
    offline = bool(payload.get("offline", False))
    state = {"last_sync_check_at": now}

    if height is not None:
        state["last_height"] = height
    if height is not None and height != previous_state.get("last_height"):
        state["last_height_changed_at"] = now
    else:
        state["last_height_changed_at"] = previous_state.get("last_height_changed_at", now)

    if offline:
        return CRIT, "monerod RPC reports offline=true", state, "rpc_offline"
    if synchronized or (height is not None and target_height is not None and target_height > 0 and height >= target_height):
        state["last_synced_at"] = now
        return OK, "monerod is synced", state, ""
    if height is None or target_height is None or target_height <= 0:
        return WARN, "monerod RPC does not report enough height data to confirm sync", state, "sync_unknown"

    stalled_for = _seconds_since(str(state["last_height_changed_at"]), now)
    state["sync_stalled_for_seconds"] = stalled_for
    stall_threshold = config.get_int("sync_stall_seconds", DEFAULT_SYNC_STALL_SECONDS)
    if height < target_height and not busy_syncing:
        return CRIT, f"monerod is behind ({height}/{target_height}) and not syncing", state, "sync_not_syncing"
    if height < target_height and stalled_for >= stall_threshold:
        return CRIT, f"monerod sync is stalled for {stalled_for}s at height {height}/{target_height}", state, "sync_stalled"
    return WARN, f"monerod sync is pending at height {height}/{target_height}", state, "sync_pending"


def _evaluate_peers(config: CheckConfig, payload: dict[str, Any]) -> tuple[str, str, str]:
    min_out_peers = config.get_int("min_out_peers", 1)
    min_in_peers = config.get_int("min_in_peers", 0)
    outgoing = _int_payload(payload, "outgoing_connections_count")
    incoming = _int_payload(payload, "incoming_connections_count")
    height = _int_payload(payload, "height")
    target_height = _int_payload(payload, "target_height")
    behind = height is not None and target_height is not None and target_height > 0 and height < target_height
    messages = []
    status = OK

    if outgoing is not None and outgoing < min_out_peers:
        messages.append(f"outgoing peers below threshold: {outgoing} < {min_out_peers}")
        status = CRIT if behind else WARN
    if incoming is not None and incoming < min_in_peers:
        messages.append(f"incoming peers below threshold: {incoming} < {min_in_peers}")
        if status != CRIT:
            status = WARN
    if messages:
        code = "out_peers_low" if outgoing is not None and outgoing < min_out_peers else "in_peers_low"
        return status, "; ".join(messages), code
    return OK, "peer thresholds pass", ""


def _rpc_details(payload: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "height",
        "target_height",
        "synchronized",
        "busy_syncing",
        "offline",
        "outgoing_connections_count",
        "incoming_connections_count",
    ]
    return {key: payload[key] for key in keys if key in payload}


def _result(
    config: CheckConfig,
    status: str,
    message: str,
    details: dict[str, Any],
    state: dict[str, Any],
    error: str = "",
    problem_codes: list[str] | None = None,
) -> CheckResult:
    result_details = dict(details)
    codes = [code for code in problem_codes or [] if code]
    if codes:
        result_details["problem_code"] = codes[0]
        result_details["problem_codes"] = codes
    if "sync_stalled_for_seconds" in state:
        result_details["sync_stalled_for_seconds"] = state["sync_stalled_for_seconds"]
    if error:
        result_details["error"] = error
    return CheckResult(name=config.section, status=status, message=message, details=result_details, state=state)


def _get_int(values: dict[str, str], key: str) -> int | None:
    value = values.get(key)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _int_payload(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _connect_host(bind_host: str) -> str:
    stripped = bind_host.strip()
    if stripped in WILDCARD_HOSTS:
        return "127.0.0.1"
    return stripped or "127.0.0.1"


def _format_url_host(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _seconds_since(previous_iso: str, now_iso: str) -> int:
    try:
        previous = datetime.fromisoformat(previous_iso.replace("Z", "+00:00"))
        now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return int((now - previous).total_seconds())
