from __future__ import annotations

import socket
import struct
import threading
import unittest
from contextlib import closing

from service_check.checks.socks_proxy.check import run
from service_check.models import CRIT, OK, UNKNOWN, CheckConfig


class SocksProxyCheckTests(unittest.TestCase):
    def test_socks_connect_success_without_auth(self) -> None:
        with FakeSocksServer() as server:
            result = run(
                CheckConfig(
                    section="threeproxy",
                    check="socks_proxy",
                    options={
                        "proxy_host": "127.0.0.1",
                        "proxy_port": str(server.port),
                        "target_host": "example.com",
                        "target_port": "80",
                        "timeout_seconds": "2",
                    },
                )
            )

        self.assertEqual(result.status, OK)
        self.assertEqual(result.details["auth_method"], "none")
        self.assertEqual(server.requested_target, ("example.com", 80))

    def test_socks_connect_success_with_userpass_auth(self) -> None:
        with FakeSocksServer(username="user", password="secret") as server:
            result = run(
                CheckConfig(
                    section="threeproxy",
                    check="socks_proxy",
                    options={
                        "proxy_host": "127.0.0.1",
                        "proxy_port": str(server.port),
                        "target_host": "192.0.2.10",
                        "target_port": "443",
                        "username": "user",
                        "password": "secret",
                        "timeout_seconds": "2",
                    },
                )
            )

        self.assertEqual(result.status, OK)
        self.assertEqual(result.details["auth_method"], "username_password")
        self.assertEqual(server.requested_target, ("192.0.2.10", 443))

    def test_socks_connect_failure_is_critical(self) -> None:
        with FakeSocksServer(connect_reply=0x05) as server:
            result = run(
                CheckConfig(
                    section="threeproxy",
                    check="socks_proxy",
                    options={
                        "proxy_host": "127.0.0.1",
                        "proxy_port": str(server.port),
                        "target_host": "example.com",
                        "target_port": "80",
                        "timeout_seconds": "2",
                    },
                )
            )

        self.assertEqual(result.status, CRIT)
        self.assertEqual(result.details["problem_code"], "socks_connect_failed")
        self.assertIn("connection refused", result.details["error"])

    def test_missing_config_is_unknown(self) -> None:
        result = run(
            CheckConfig(
                section="threeproxy",
                check="socks_proxy",
                options={"proxy_host": "127.0.0.1", "proxy_port": "1080"},
            )
        )

        self.assertEqual(result.status, UNKNOWN)
        self.assertEqual(result.details["problem_code"], "missing_config")


class FakeSocksServer:
    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        connect_reply: int = 0,
    ) -> None:
        self.username = username
        self.password = password
        self.connect_reply = connect_reply
        self.requested_target: tuple[str, int] | None = None
        self._ready = threading.Event()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._server: socket.socket | None = None
        self.port = 0

    def __enter__(self) -> FakeSocksServer:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve_once, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._server is not None:
            self._server.close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _serve_once(self) -> None:
        assert self._server is not None
        self._ready.set()
        try:
            conn, _addr = self._server.accept()
        except OSError:
            return
        with closing(conn):
            conn.settimeout(2)
            self._handle_client(conn)
            self._done.set()

    def _handle_client(self, conn: socket.socket) -> None:
        version, method_count = _recv_exact(conn, 2)
        assert version == 5
        methods = _recv_exact(conn, method_count)
        if self.username is None:
            assert 0 in methods
            conn.sendall(b"\x05\x00")
        else:
            assert 2 in methods
            conn.sendall(b"\x05\x02")
            self._handle_userpass(conn)

        header = _recv_exact(conn, 4)
        assert header[:3] == b"\x05\x01\x00"
        target_host = self._read_address(conn, header[3])
        target_port = struct.unpack("!H", _recv_exact(conn, 2))[0]
        self.requested_target = (target_host, target_port)
        conn.sendall(bytes([5, self.connect_reply, 0, 1]) + socket.inet_aton("127.0.0.1") + struct.pack("!H", 1080))

    def _handle_userpass(self, conn: socket.socket) -> None:
        version, username_length = _recv_exact(conn, 2)
        assert version == 1
        username = _recv_exact(conn, username_length).decode()
        password_length = _recv_exact(conn, 1)[0]
        password = _recv_exact(conn, password_length).decode()
        status = 0 if (username, password) == (self.username, self.password) else 1
        conn.sendall(bytes([1, status]))

    def _read_address(self, conn: socket.socket, address_type: int) -> str:
        if address_type == 1:
            return socket.inet_ntoa(_recv_exact(conn, 4))
        if address_type == 3:
            length = _recv_exact(conn, 1)[0]
            return _recv_exact(conn, length).decode("idna")
        if address_type == 4:
            return socket.inet_ntop(socket.AF_INET6, _recv_exact(conn, 16))
        raise AssertionError(f"unexpected address type: {address_type}")


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise AssertionError("unexpected EOF")
        chunks.extend(chunk)
    return bytes(chunks)


if __name__ == "__main__":
    unittest.main()
