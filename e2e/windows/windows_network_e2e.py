#!/usr/bin/env python3
"""Exercise real TCP, UDP and WebSocket traffic through a native Windows exporter.

Only loopback peers created by this runner are used. Each workflow runs in a
bounded child process; no driver, transport or subprocess function is mocked.
"""

import argparse
import json
import platform
import socket
import socketserver
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from windows_fixture import NativeExporter, isolated_environment, run_bounded

PREFIX = "NETWORK_RESULT="
CASES = ("tcp", "udp", "websocket")


class TcpEcho(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(10)
        while data := self.request.recv(65536):
            self.request.sendall(data)


class UdpEcho(socketserver.BaseRequestHandler):
    def handle(self):
        data, stream = self.request
        stream.sendto(data, self.client_address)


@contextmanager
def socket_peer(kind):
    server_class, handler = {
        "tcp": (socketserver.ThreadingTCPServer, TcpEcho),
        "udp": (socketserver.ThreadingUDPServer, UdpEcho),
    }[kind]
    with server_class(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield server.server_address
        finally:
            server.shutdown()
            thread.join(5)
            assert not thread.is_alive(), "fixture listener did not stop"


@contextmanager
def websocket_peer():
    from websockets.sync.server import serve

    def echo(connection):
        for message in connection:
            connection.send(message)

    with serve(echo, "127.0.0.1", 0, close_timeout=2) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield server.socket.getsockname()
        finally:
            server.shutdown()
            thread.join(5)
            assert not thread.is_alive(), "WebSocket listener did not stop"


def roundtrip(address, identifier):
    # Multiple chunks exceed common transport buffers. Each concurrent stream
    # has different content so accidental cross-connection delivery is detected.
    payload = bytes(range(256)) * 256 + identifier.to_bytes(4)
    with socket.create_connection(address, timeout=10) as stream:
        for _ in range(8):
            stream.sendall(payload)
            received = bytearray()
            while len(received) < len(payload):
                data = stream.recv(len(payload) - len(received))
                assert data, "TCP stream closed before all bytes arrived"
                received.extend(data)
            assert received == payload, "binary payload changed"
    return len(payload) * 8


def run_case(case, directory):
    from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

    checks = []
    peer = websocket_peer() if case == "websocket" else socket_peer(case)
    with peer as address:
        config = {"host": address[0], "port": address[1]}
        class_name = {"tcp": "TcpNetwork", "udp": "UdpNetwork", "websocket": "WebsocketNetwork"}[case]
        if case == "websocket":
            config = {"url": f"ws://{address[0]}:{address[1]}"}
        export = {"network": {"type": f"jumpstarter_driver_network.driver.{class_name}", "config": config}}
        with NativeExporter(
            export=export, directory=directory / "exporter",
            allowed=["jumpstarter_driver_network.client.NetworkClient"],
        ) as exporter, exporter.client() as root:
            client = root.children["network"]
            scheme = "ws" if case == "websocket" else case
            assert client.address() == f"{scheme}://{address[0]}:{address[1]}"
            checks.append("actual endpoint discovery through RPC")
            if case == "tcp":
                with TcpPortforwardAdapter(client=client, local_host="127.0.0.1", local_port=0) as forwarded:
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        sizes = list(pool.map(lambda i: roundtrip(forwarded, i), range(4)))
                    checks.append(f"four simultaneous TCP forwards: {sum(sizes)} exact binary bytes")
                    assert roundtrip(forwarded, 99) > 0
                    checks.append("new connection after all previous connections closed")
                try:
                    with socket.create_connection(forwarded, timeout=5):
                        raise AssertionError("forwarding listener survived context exit")
                except ConnectionRefusedError:
                    pass
                checks.append("forwarding listener removed on context exit")
            else:
                sizes = (1, 512, 1400, 8192) if case == "udp" else (1, 512, 65536, 131072)
                for _ in range(2):
                    with client.stream() as stream:
                        for size in sizes:
                            payload = (bytes(range(256)) * ((size // 256) + 1))[:size]
                            stream.send(payload)
                            assert stream.receive() == payload, f"{case} framing changed at {size} bytes"
                checks.append(f"exact binary message boundaries at sizes {sizes}")
                checks.append("close and reopen client stream against same peer")
        assert exporter.cleanup_verified and exporter.worker_pids
        checks.append("stock exporter Ctrl+Break exit149, worker and listener removed")
    return {"checks": checks, "exporter_exit_code": exporter.exit_code, "cleanup_verified": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("this runner requires native Windows")
    if args.worker:
        result = {"case": args.case, "status": "FAIL"}
        try:
            with tempfile.TemporaryDirectory(prefix="jmp-network-") as directory:
                result.update(run_case(args.case, Path(directory)))
            assert not Path(directory).exists()
            result.update(status="PASS", temporary_directory_removed=True)
        except Exception:  # noqa: BLE001 - record all workflow failures
            result["error"] = traceback.format_exc()
        print(PREFIX + json.dumps(result), flush=True)
        return 0 if result["status"] == "PASS" else 1
    results = []
    for case in (args.case,) if args.case else CASES:
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--case", case]
        started = time.monotonic()
        try:
            completed = run_bounded(command, isolated_environment(), args.timeout)
            envelopes = [line[len(PREFIX):] for line in completed.stdout.splitlines() if line.startswith(PREFIX)]
            assert len(envelopes) == 1, completed.stderr[-4000:]
            result = json.loads(envelopes[0])
            assert result["case"] == case
            assert completed.returncode == (0 if result["status"] == "PASS" else 1)
        except Exception:  # noqa: BLE001 - watchdog failures must fail the report
            result = {"case": case, "status": "FAIL", "error": traceback.format_exc()}
        result.update(command=command, seconds=round(time.monotonic() - started, 3))
        results.append(result)
        print(f"{result['status']}: {case}", flush=True)
    report = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "environment": {"python": sys.version, "platform": platform.platform(), "packages": {
            package: version(package) for package in ("jumpstarter", "jumpstarter-core", "grpcio", "websockets")
        }},
        "execution": "Native Windows stock jmp run and NetworkClient; real loopback protocol peers",
        "results": results,
        "summary": {"passed": sum(r["status"] == "PASS" for r in results),
                    "failed": sum(r["status"] != "PASS" for r in results)},
        "limitations": ["IPv4 plaintext loopback only; IPv6, TLS, WAN failures and throughput not qualified.",
                        "UnixNetwork, D-Bus, VsockNetwork and their adapters are outside this run.",
                        "Datagram sizes and message boundaries are checked; UDP loss/reordering is not simulated."],
    }
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return bool(report["summary"]["failed"])


if __name__ == "__main__":
    raise SystemExit(main())
