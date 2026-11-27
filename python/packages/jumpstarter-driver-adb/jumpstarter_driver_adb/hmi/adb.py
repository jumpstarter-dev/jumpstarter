"""The adb commands the HMI needs, against the ADB server its device shares.

The device, its serial, the ADB server and its port are the parent :class:`~jumpstarter_driver_adb.driver.AdbDevice`'s:
this module only runs commands. Every command that may need the server takes its environment from the parent's
``adb_env()``, which takes a reference to the shared server (so that a server nobody owns is never started by the adb
client) and is released with the parent. The commands that only clean up use an environment that points at the server
as a *remote* one, which adb never starts: a command that runs after the parent has taken its server down must not
bring up another.

Nothing here needs root on the device: the scrcpy server runs as the ``shell`` user, from ``/data/local/tmp``.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class AdbError(RuntimeError):
    """An adb command failed (the message says what to look at)."""


def parse_wm_size(text: str) -> tuple[int, int] | None:
    """The display's size from ``wm size`` (``Physical size: 1080x2340``, and ``Override size`` when there is one, which
    wins)."""
    override = re.search(r"Override size:\s*(\d+)x(\d+)", text)
    physical = re.search(r"Physical size:\s*(\d+)x(\d+)", text)
    found = override or physical
    return (int(found.group(1)), int(found.group(2))) if found else None


def _server_pattern(marker: str) -> str:
    """A pattern for ``pgrep``/``pkill -f`` that matches the server's command line and not the shell command that
    carries the pattern (``Serve[r]`` matches "Server" and is not the text "Serve[r]")."""
    return f"scrcpy[.]Serve[r] .*{marker}"


def _checked_marker(marker: str) -> str:
    if not re.fullmatch(r"scid=[0-9a-f]{8}", marker):
        raise ValueError(f"not a server marker: {marker!r}")
    return marker


@dataclass
class Adb:
    """The adb commands of one device's HMI, against one ADB server.

    ``env_factory`` is the parent's ``adb_env`` (it holds a reference to the shared server); without it the commands use
    the plain environment of the server's port.
    """

    adb_path: str
    server_port: int = 15037
    env_factory: Callable[[], dict[str, str]] | None = None
    timeout: float = 30.0

    @classmethod
    def of(cls, device: Any) -> Adb:
        """The commands of ``device`` (an ``AdbDevice``): its adb, its server, its timeout."""
        return cls(
            adb_path=device.adb_path,
            server_port=device.server_port,
            env_factory=device.adb_env,
            timeout=device.connect_timeout,
        )

    def env(self, *, autostart: bool = True) -> dict[str, str]:
        """The environment that points ``adb`` at the server.

        With ``autostart=False`` adb is told the server is a *remote* one (``ADB_SERVER_SOCKET``), which it never
        starts, and the shared server is not referenced: for a command that only cleans up.
        """
        if not autostart:
            return {**os.environ, "ADB_SERVER_SOCKET": f"tcp:127.0.0.1:{self.server_port}"}
        if self.env_factory is not None:
            return self.env_factory()
        return {**os.environ, "ANDROID_ADB_SERVER_PORT": str(self.server_port)}

    def run(
        self, args: list[str], *, timeout: float | None = None, check: bool = True, autostart: bool = True
    ) -> subprocess.CompletedProcess:
        """``adb ARGS`` against the server, bounded.

        Raises:
            AdbError: If it failed (and ``check``), timed out or could not be run.
        """
        limit = timeout or self.timeout
        try:
            result = subprocess.run(
                [self.adb_path, *args],
                capture_output=True,
                text=True,
                timeout=limit,
                env=self.env(autostart=autostart),
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise AdbError(f"`adb {' '.join(args[:3])}` timed out after {limit:g}s") from e
        except OSError as e:
            raise AdbError(f"could not run adb: {e}") from e
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise AdbError(f"`adb {' '.join(args[:4])}` failed: {detail or result.returncode}")
        return result

    # ---- per-device operations ------------------------------------------------------------------------------

    def push(self, serial: str, local: Path, remote: str) -> None:
        """Copy a file to the device."""
        self.run(["-s", serial, "push", str(local), remote])

    def remove(self, serial: str, remote: str, *, timeout: float = 10) -> None:
        """Delete a file on the device (best effort; never starts an ADB server)."""
        self.run(
            ["-s", serial, "shell", "rm", "-f", shlex.quote(remote)], check=False, timeout=timeout, autostart=False
        )

    def device_sha256(self, serial: str, remote: str) -> str | None:
        """The SHA-256 of a file on the device (its own ``sha256sum``), or ``None`` when there is no such file or no
        such command."""
        command = f"sha256sum {shlex.quote(remote)} 2>/dev/null"
        out = self.run(["-s", serial, "shell", command], check=False, timeout=15).stdout
        word = out.split()[0].lower() if out.split() else ""
        return word if re.fullmatch(r"[0-9a-f]{64}", word) else None

    def move(self, serial: str, source: str, target: str) -> None:
        """``mv -f`` on the device (a pushed file into its place)."""
        self.run(["-s", serial, "shell", f"mv -f {shlex.quote(source)} {shlex.quote(target)}"], timeout=15)

    def kill_server_process(self, serial: str, marker: str, *, timeout: float = 10) -> None:
        """End the scrcpy-server that was started with ``marker`` (its ``scid=...``) if it is still there; never starts
        an ADB server.

        ``pkill -f`` matches the whole command line, so the marker names this session's server and no other (a user's
        own scrcpy has another scid).
        """
        pattern = _server_pattern(_checked_marker(marker))
        self.run(["-s", serial, "shell", f"pkill -f '{pattern}'"], check=False, timeout=timeout, autostart=False)

    def server_alive(self, serial: str, marker: str) -> bool | None:
        """Whether the scrcpy-server started with ``marker`` is still running on the device: ``True``, ``False``, or
        ``None`` when the device (or adb) did not answer at all, which is not the same as the server being gone."""
        pattern = _server_pattern(_checked_marker(marker))
        try:
            done = self.run(["-s", serial, "shell", f"pgrep -f '{pattern}'"], check=False, timeout=10, autostart=False)
        except AdbError:
            return None
        if done.returncode not in (0, 1):  # pgrep: 0 found, 1 none; anything else is adb or the shell failing
            return None
        return done.returncode == 0 and done.stdout.strip() != ""

    def forward(self, serial: str, abstract_name: str) -> int:
        """Forward a free exporter TCP port to the device's abstract socket; the port.

        ``tcp:0`` has the ADB server choose the port, so no range has to be kept clear of whatever else runs on the
        exporter.
        """
        before = self.forward_list(serial)
        out = self.run(["-s", serial, "forward", "tcp:0", f"localabstract:{abstract_name}"]).stdout
        for line in reversed(out.strip().splitlines()):
            if line.strip().isdigit() and 0 < int(line) < 65536:
                return int(line)
        appeared = {local for local, remote in self.forward_list(serial)} - {local for local, _ in before}
        if len(appeared) == 1:
            return int(appeared.pop().removeprefix("tcp:"))
        raise AdbError(f"adb forward did not say which port it chose (printed {out.strip()!r})")

    def forward_list(self, serial: str) -> list[tuple[str, str]]:
        """``(local, remote)`` of the forwards this device has on the server (``tcp:1234``, ``localabstract:name``)."""
        out = self.run(["forward", "--list"], check=False).stdout
        result = []
        for line in out.splitlines():
            fields = line.split()
            if len(fields) >= 3 and fields[0] == serial:
                result.append((fields[1], fields[2]))
        return result

    def forward_remove(self, serial: str, port: int, *, timeout: float = 10) -> None:
        """Remove a forward (best effort: it may be gone with the device; never starts an ADB server)."""
        self.run(["-s", serial, "forward", "--remove", f"tcp:{port}"], check=False, timeout=timeout, autostart=False)

    def drop_stale_forwards(self, serial: str, abstract_name: str) -> int:
        """Remove forwards to this HMI's own socket name that an earlier run left behind (it died before it could
        clean up)."""
        stale = [local for local, remote in self.forward_list(serial) if remote == f"localabstract:{abstract_name}"]
        for local in stale:
            self.run(["-s", serial, "forward", "--remove", local], check=False, timeout=10)
        return len(stale)

    def shell(self, serial: str, command: str, *, timeout: float | None = None) -> str:
        """Run a shell command on the device; its output."""
        return self.run(["-s", serial, "shell", command], timeout=timeout).stdout

    def spawn(self, serial: str, command: str) -> subprocess.Popen:
        """Start a long-running shell command on the device. Its stdout and stderr come back on one pipe."""
        return subprocess.Popen(
            [self.adb_path, "-s", serial, "shell", command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=self.env(),
            text=True,
            errors="replace",
        )

    def keyevent(self, serial: str, code: int, *, long: bool = False) -> None:
        """``input keyevent`` on the device: a key press without the scrcpy server (a plain shell command)."""
        flag = "--longpress " if long else ""
        self.run(["-s", serial, "shell", f"input keyevent {flag}{int(code)}"], timeout=10)

    def rotation_state(self, serial: str) -> tuple[bool, int]:
        """``(auto_rotate, user_rotation)``: whether the sensor decides, and the rotation a lock holds (0-3)."""
        read = ["-s", serial, "shell"]
        auto = self.run([*read, "settings get system accelerometer_rotation"], check=False, timeout=10).stdout
        raw = self.run([*read, "settings get system user_rotation"], check=False, timeout=10).stdout.strip()
        return auto.strip() == "1", int(raw) if raw.isdigit() and int(raw) < 4 else 0

    @staticmethod
    def _wm_failed(done: subprocess.CompletedProcess) -> bool:
        return done.returncode != 0 or "error" in (done.stdout + done.stderr).lower() or "sage" in done.stdout

    def lock_rotation(self, serial: str, rotation: int, display_id: int = 0) -> None:
        """Hold the display at ``rotation`` (0-3) and switch auto-rotate off: ``wm user-rotation lock``, or the two
        settings it sets."""
        if not 0 <= rotation <= 3:
            raise ValueError(f"rotation must be 0 to 3: {rotation}")
        where = f"-d {int(display_id)} " if display_id else ""
        done = self.run(["-s", serial, "shell", f"wm user-rotation {where}lock {rotation}"], check=False, timeout=10)
        if self._wm_failed(done):
            settings = f"settings put system accelerometer_rotation 0; settings put system user_rotation {rotation}"
            self.run(["-s", serial, "shell", settings], timeout=10)

    def free_rotation(self, serial: str, display_id: int = 0) -> None:
        """Give the rotation back to the sensor."""
        where = f"-d {int(display_id)} " if display_id else ""
        done = self.run(["-s", serial, "shell", f"wm user-rotation {where}free"], check=False, timeout=10)
        if self._wm_failed(done):
            self.run(["-s", serial, "shell", "settings put system accelerometer_rotation 1"], timeout=10)

    def prop(self, serial: str, name: str) -> str:
        """A system property of the device (``ro.build.version.release``), or an empty string."""
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", name):
            raise ValueError(f"not a property name: {name!r}")
        return self.run(["-s", serial, "shell", f"getprop {name}"], check=False, timeout=10).stdout.strip()
