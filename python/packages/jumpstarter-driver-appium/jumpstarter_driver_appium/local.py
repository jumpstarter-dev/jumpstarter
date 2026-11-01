"""Appium on the client, for targets only the client can reach."""

import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Generator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path


def find_appium(executable: str) -> str:
    """Resolve the user's Appium launcher, or explain how to get one."""
    resolved = shutil.which(executable)
    if resolved is None:
        raise RuntimeError(
            f"Appium launcher {executable!r} was not found on this machine; install Appium and its "
            "drivers (Jumpstarter does not bundle them) or pass its path"
        )
    return resolved


# Proxy settings, common on managed Macs, must not divert a loopback probe.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _ready(url: str) -> bool:
    try:
        with _DIRECT.open(f"{url}/status", timeout=2) as response:
            return json.load(response).get("value", {}).get("ready") is True
    except (OSError, ValueError, AttributeError):
        return False


@contextmanager
def local_appium(
    executable: str = "appium", *, env: Mapping[str, str] | None = None, timeout: float = 60.0
) -> Generator[str, None, None]:
    """Run the user's installed Appium on a loopback port and yield its URL.

    Appium uses the caller's environment, so its ``APPIUM_HOME`` and installed
    drivers apply. It stops when the context closes.
    """
    launcher = find_appium(executable)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="jumpstarter-appium-") as directory:
        log_path = Path(directory) / "appium.log"
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [launcher, "server", "--address", "127.0.0.1", "--port", str(port), "--log-no-colors"],
                env=dict(os.environ if env is None else env),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        try:
            deadline = time.monotonic() + timeout
            while not _ready(url):
                if process.poll() is not None or time.monotonic() >= deadline:
                    tail = log_path.read_text(errors="replace")[-2048:].strip()
                    problem = "exited" if process.poll() is not None else f"was not ready within {timeout:.0f} seconds"
                    raise RuntimeError(f"Appium {problem}:\n{tail or '(no output)'}")
                time.sleep(0.2)
            yield url
        finally:
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
