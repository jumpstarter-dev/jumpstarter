"""Shared stock-exporter lifecycle for hardware-free Windows protocol checks.

Callers supply only disposable drivers and loopback services. Run each workflow
in a bounded subprocess: a deadline on RPC operations cannot protect Python
interpreter teardown. No driver or transport is replaced with a mock here.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

from windows_client_e2e import isolated_environment, run_bounded  # noqa: F401 - shared harness exports
from windows_exporter_e2e import assert_clean, endpoint_open, wait_endpoint


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class NativeExporter:
    """Own a stock Windows exporter and verify its graceful shutdown."""

    def __init__(self, *, export, directory, allowed, timeout=30, extra_env=None):
        if sys.platform != "win32":
            raise RuntimeError("NativeExporter requires Windows")
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=False)
        self.timeout = timeout
        self.allowed = sorted({"jumpstarter_driver_composite.client.CompositeClient", *allowed})
        self.address = ("127.0.0.1", free_port())
        self.endpoint = f"{self.address[0]}:{self.address[1]}"
        self.environment = isolated_environment()
        self.environment.update(extra_env or {})
        self.environment["JMP_CLIENT_CONFIG_HOME"] = str(self.directory / "client-config")
        self.environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + self.environment.get("PATH", "")
        self.environment["PYTHONPATH"] = (
            str(Path(__file__).resolve().parent) + os.pathsep + self.environment.get("PYTHONPATH", "")
        )
        config = {
            "apiVersion": "jumpstarter.dev/v1alpha1", "kind": "ExporterConfig",
            "metadata": {"name": "windows-protocol-fixture", "namespace": "default"}, "export": export,
        }
        path = self.directory / "exporter.json"
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        self.command = [
            sys.executable, "-m", "jumpstarter_cli", "--log-format", "json", "--log-level", "DEBUG",
            "run", "--exporter-config", str(path), "--tls-grpc-listener", self.endpoint, "--tls-grpc-insecure",
        ]
        self.process = None
        self.logs = ExitStack()
        self.exit_code = None
        self.cleanup_verified = False
        self.worker_pids = set()

    def __enter__(self):
        try:
            stdout = self.logs.enter_context((self.directory / "stdout.log").open("wb"))
            stderr = self.logs.enter_context((self.directory / "stderr.log").open("wb"))
            self.process = subprocess.Popen(
                self.command, env=self.environment, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            wait_endpoint(self.process, self.address, self.timeout)
            return self
        except BaseException:
            self._terminate()
            self.logs.close()
            raise

    @contextmanager
    def client(self):
        from anyio.from_thread import start_blocking_portal

        from jumpstarter.client import client_from_path

        with (
            start_blocking_portal() as portal,
            ExitStack() as stack,
            portal.wrap_async_context_manager(client_from_path(
                self.endpoint, portal, stack, allow=self.allowed, unsafe=False, insecure=True,
            )) as client,
        ):
            yield client

    def _terminate(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()  # Its Job Object owns all worker descendants.
            self.process.wait(timeout=5)

    def __exit__(self, exc_type, exc, traceback):
        try:
            if self.process.poll() is None:
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
                self.process.wait(timeout=self.timeout + 5)
            self.exit_code = self.process.returncode
        finally:
            self._terminate()
            self.logs.close()
        for line in (self.directory / "stderr.log").read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("msg") == "Windows exporter worker started":
                self.worker_pids.add(int(record["pid"]))
        if not self.worker_pids:
            raise AssertionError(f"No worker PID observed; cannot verify process cleanup: {self.directory}")
        assert_clean(self.address, self.worker_pids)
        self.cleanup_verified = not endpoint_open(self.address)
        if exc_type is None and self.exit_code != 0:
            raise AssertionError(f"Exporter failed graceful shutdown: exit {self.exit_code}; logs in {self.directory}")


def wait_until(condition, *, timeout=10, message="condition did not become true"):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise TimeoutError(message)
        time.sleep(0.02)
