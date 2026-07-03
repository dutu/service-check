from __future__ import annotations

import socket
import struct
import time

from service_check.models import CRIT, OK, UNKNOWN, CheckConfig, CheckResult

SOCKS_VERSION = 5
AUTH_NONE = 0x00
AUTH_USERPASS = 0x02
AUTH_NO_ACCEPTABLE = 0xFF
USERPASS_VERSION = 1
USERPASS_SUCCESS = 0
COMMAND_CONNECT = 0x01
ADDRESS_IPV4 = 0x01
ADDRESS_DOMAIN = 0x03
ADDRESS_IPV6 = 0x04
REPLY_SUCCESS = 0x00

SOCKS_REPLIES = {
    0x01: "general SOCKS server failure",
    0x02: "connection not allowed by ruleset",
    0x03: "network unreachable",
    0x04: "host unreachable",
    0x05: "connection refused",
    0x06: "TTL expired",
    0x07: "command not supported",
    0x08: "address type not supported",
}

CHECK_METADATA = {
    "description": "Checks SOCKS5 proxy functionality by opening a CONNECT tunnel to a configured target.",
    "statuses": {
        OK: "SOCKS5 handshake and CONNECT request succeeded.",
        CRIT: "Proxy connection, authentication, or CONNECT request failed.",
        UNKNOWN: "Required config is missing or invalid.",
    },
    "details": {
        "problem_code": "Primary machine-readable problem reason.",
        "problem_codes": "List of machine-readable problem reasons.",
        "proxy_host": "Configured SOCKS proxy hostname or IP address.",
        "proxy_port": "Configured SOCKS proxy TCP port number.",
        "target_host": "Target hostname or IP address requested through the proxy.",
        "target_port": "Target TCP port requested through the proxy.",
        "timeout_seconds": "Network timeout used by the check.",
        "elapsed_ms": "End-to-end check duration in milliseconds.",
        "auth_method": "SOCKS5 authentication method selected by the proxy.",
        "bound_address": "Bound address returned by the proxy on successful CONNECT.",
        "bound_port": "Bound port returned by the proxy on successful CONNECT.",
        "error": "Socket/protocol/config error text; present on failure.",
    },
}


def run(config: CheckConfig) -> CheckResult:
    proxy_host = config.get("proxy_host") or config.get("host")
    proxy_port_value = config.get("proxy_port") or config.get("port")
    target_host = config.get("target_host")
    target_port_value = config.get("target_port")
    timeout_seconds = config.get_float("timeout_seconds", 5.0)

    missing = [
        key
        for key, value in (
            ("proxy_host", proxy_host),
            ("proxy_port", proxy_port_value),
            ("target_host", target_host),
            ("target_port", target_port_value),
        )
        if not value
    ]
    if missing:
        return _unknown(config, "missing_config", f"socks_proxy check requires {', '.join(missing)}")

    try:
        proxy_port = int(proxy_port_value or "")
        target_port = int(target_port_value or "")
        if not _valid_port(proxy_port) or not _valid_port(target_port):
            raise ValueError("ports must be between 1 and 65535")
    except ValueError as exc:
        return _unknown(config, "invalid_port", f"socks_proxy check requires valid numeric ports: {exc}")

    details = {
        "proxy_host": proxy_host,
        "proxy_port": proxy_port,
        "target_host": target_host,
        "target_port": target_port,
        "timeout_seconds": timeout_seconds,
    }

    started = time.monotonic()
    try:
        with socket.create_connection((proxy_host, proxy_port), timeout=timeout_seconds) as sock:
            sock.settimeout(timeout_seconds)
            auth_method = _negotiate_auth(sock, config)
            details["auth_method"] = _auth_method_name(auth_method)
            bound_address, bound_port = _connect_via_socks(sock, target_host or "", target_port)
            details["bound_address"] = bound_address
            details["bound_port"] = bound_port
            details["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            return CheckResult(
                name=config.section,
                status=OK,
                message=f"SOCKS proxy {proxy_host}:{proxy_port} connected to {target_host}:{target_port}",
                details=details,
            )
    except (OSError, SocksProtocolError) as exc:
        details["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        details["error"] = str(exc)
        details["problem_code"] = "socks_connect_failed"
        details["problem_codes"] = ["socks_connect_failed"]
        return CheckResult(
            name=config.section,
            status=CRIT,
            message=f"SOCKS proxy {proxy_host}:{proxy_port} could not connect to {target_host}:{target_port}",
            details=details,
        )


class SocksProtocolError(Exception):
    pass


def _negotiate_auth(sock: socket.socket, config: CheckConfig) -> int:
    username = config.get("username")
    password = config.get("password")
    methods = [AUTH_NONE]
    if username is not None or password is not None:
        methods.append(AUTH_USERPASS)

    sock.sendall(bytes([SOCKS_VERSION, len(methods), *methods]))
    response = _recv_exact(sock, 2)
    if response[0] != SOCKS_VERSION:
        raise SocksProtocolError(f"invalid SOCKS version in auth response: {response[0]}")
    method = response[1]
    if method == AUTH_NO_ACCEPTABLE:
        raise SocksProtocolError("proxy rejected all offered authentication methods")
    if method == AUTH_USERPASS:
        _authenticate_userpass(sock, username or "", password or "")
    elif method != AUTH_NONE:
        raise SocksProtocolError(f"proxy selected unsupported authentication method: {method}")
    return method


def _authenticate_userpass(sock: socket.socket, username: str, password: str) -> None:
    username_bytes = username.encode("utf-8")
    password_bytes = password.encode("utf-8")
    if len(username_bytes) > 255 or len(password_bytes) > 255:
        raise SocksProtocolError("SOCKS username/password must be at most 255 bytes each")
    sock.sendall(
        bytes([USERPASS_VERSION, len(username_bytes)])
        + username_bytes
        + bytes([len(password_bytes)])
        + password_bytes
    )
    response = _recv_exact(sock, 2)
    if response[0] != USERPASS_VERSION:
        raise SocksProtocolError(f"invalid username/password auth version: {response[0]}")
    if response[1] != USERPASS_SUCCESS:
        raise SocksProtocolError("username/password authentication failed")


def _connect_via_socks(sock: socket.socket, target_host: str, target_port: int) -> tuple[str, int]:
    address_type, encoded_address = _encode_target_host(target_host)
    request = (
        bytes([SOCKS_VERSION, COMMAND_CONNECT, 0x00, address_type])
        + encoded_address
        + struct.pack("!H", target_port)
    )
    sock.sendall(request)

    header = _recv_exact(sock, 4)
    if header[0] != SOCKS_VERSION:
        raise SocksProtocolError(f"invalid SOCKS version in connect response: {header[0]}")
    reply = header[1]
    if reply != REPLY_SUCCESS:
        reason = SOCKS_REPLIES.get(reply, f"unknown SOCKS reply {reply}")
        raise SocksProtocolError(f"SOCKS CONNECT failed: {reason}")

    bound_address = _read_socks_address(sock, header[3])
    bound_port = struct.unpack("!H", _recv_exact(sock, 2))[0]
    return bound_address, bound_port


def _encode_target_host(host: str) -> tuple[int, bytes]:
    try:
        return ADDRESS_IPV4, socket.inet_pton(socket.AF_INET, host)
    except OSError:
        pass
    try:
        return ADDRESS_IPV6, socket.inet_pton(socket.AF_INET6, host)
    except OSError:
        pass

    host_bytes = host.encode("idna")
    if not host_bytes or len(host_bytes) > 255:
        raise SocksProtocolError("target_host must encode to a 1-255 byte domain name")
    return ADDRESS_DOMAIN, bytes([len(host_bytes)]) + host_bytes


def _read_socks_address(sock: socket.socket, address_type: int) -> str:
    if address_type == ADDRESS_IPV4:
        return socket.inet_ntop(socket.AF_INET, _recv_exact(sock, 4))
    if address_type == ADDRESS_IPV6:
        return socket.inet_ntop(socket.AF_INET6, _recv_exact(sock, 16))
    if address_type == ADDRESS_DOMAIN:
        length = _recv_exact(sock, 1)[0]
        return _recv_exact(sock, length).decode("idna")
    raise SocksProtocolError(f"proxy returned unsupported bound address type: {address_type}")


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise SocksProtocolError("unexpected EOF from proxy")
        chunks.extend(chunk)
    return bytes(chunks)


def _unknown(config: CheckConfig, problem_code: str, message: str) -> CheckResult:
    return CheckResult(
        name=config.section,
        status=UNKNOWN,
        message=message,
        details={"problem_code": problem_code, "problem_codes": [problem_code]},
    )


def _valid_port(port: int) -> bool:
    return 1 <= port <= 65535


def _auth_method_name(method: int) -> str:
    if method == AUTH_NONE:
        return "none"
    if method == AUTH_USERPASS:
        return "username_password"
    return str(method)
