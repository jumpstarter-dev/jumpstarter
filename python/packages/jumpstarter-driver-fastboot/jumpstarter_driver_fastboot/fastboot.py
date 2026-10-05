"""Async wrapper around the Android platform-tools ``fastboot`` CLI.

Shared by the driver (enter, preflight) and the job runner (commit).

Device pinning follows ``jumpstarter_driver_adb.driver.AdbDevice``: a device
is configured by exactly one of ``usb_port`` (the bench USB port, preferred)
or ``serial``. For ``usb_port`` the serial is looked up from
``fastboot devices -l`` on every command, and the command is addressed with
``-s SERIAL``. Identity is the bench port, so hardware can be swapped without a
config change, and a re-enumeration that changes the serial is picked up.
Nothing here ever picks "the first device found".
"""

from __future__ import annotations

import asyncio
import asyncio.subprocess
import contextlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

GETVAR_PREFIX = re.compile(r"^\(bootloader\)\s+")
READY_STATE = "fastboot"


def normalize_usb_port(usb_port: str) -> str:
    """Return ``usb_port`` in the exact form ``fastboot devices -l`` reports.

    Same rule as ``AdbDevice``: the devpath is ``usb:<path>``, the sysfs bus-port
    name on Linux (``usb:1-4.2``) and, on macOS, the IOKit location ID in decimal
    with a literal ``X`` suffix (``usb:538116096X``). Copy the value from
    ``fastboot devices -l`` (or ``adb devices -l``, it is the same port) rather
    than converting anything. Config may omit the ``usb:`` prefix; matching is
    exact string equality, so it is normalized once here.
    """
    port = str(usb_port).strip()
    if not port:
        raise ValueError("'usb_port' must not be empty")
    return port if port.startswith("usb:") else f"usb:{port}"


class FastbootError(RuntimeError):
    """A fastboot command failed (retried by the job runner)."""


class FastbootStalled(FastbootError):
    """A fastboot command produced no output for longer than the stall timeout."""


@dataclass(frozen=True)
class FastbootDevice:
    serial: str
    state: str
    usb: str | None = None


@dataclass(frozen=True)
class Result:
    returncode: int
    output: str


def parse_devices(text: str) -> list[FastbootDevice]:
    """Parse ``fastboot devices -l`` output."""
    devices = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        usb = next((p for p in parts[2:] if p.startswith("usb:")), None)
        devices.append(FastbootDevice(serial=parts[0], state=parts[1], usb=usb))
    return devices


def parse_getvar(name: str, output: str) -> str | None:
    """Extract ``name: value`` from ``fastboot getvar`` output."""
    prefix = f"{name}:"
    for raw in output.splitlines():
        line = GETVAR_PREFIX.sub("", raw.strip())
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def parse_size(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value, 0)
    except ValueError:
        return None


@dataclass
class Fastboot:
    """One device, pinned by exactly one of ``usb_port`` (preferred) or ``serial``."""

    usb_port: str | None = None
    serial: str | None = None
    binary: str = "fastboot"
    command_timeout: float = 30.0

    def __post_init__(self):
        if (self.usb_port is None) == (self.serial is None):
            raise ValueError("exactly one of 'usb_port' (the bench USB port, preferred) or 'serial' is required")
        if self.usb_port is not None:
            self.usb_port = normalize_usb_port(self.usb_port)
        elif not str(self.serial).strip():
            raise ValueError("'serial' must not be empty")

    @property
    def label(self) -> str:
        """The bench identity of this device: its USB port, or its configured serial."""
        return self.usb_port or str(self.serial)

    async def list_devices(self) -> list[FastbootDevice]:
        result = await self._exec(["devices", "-l"], timeout=self.command_timeout)
        return parse_devices(result.output)

    async def resolve(self) -> str:
        """The serial to address this device by, resolved fresh on every call.

        Raises ``FastbootError`` if the device is not in fastboot right now.
        """
        visible = await self.list_devices()
        for device in visible:
            matches = device.usb == self.usb_port if self.usb_port else device.serial == self.serial
            if not matches:
                continue
            if device.state != READY_STATE:
                raise FastbootError(
                    f"device on {self.label} is '{device.state}', not ready. "
                    "If it reports no permissions, install the fastboot udev rules (or run the exporter "
                    "with access to /dev/bus/usb)."
                )
            return device.serial

        where = f"USB port {self.usb_port}" if self.usb_port else f"serial {self.serial}"
        message = (
            f"no fastboot device on {where}. It may be powered off, booted normally rather than into "
            "fastboot, or plugged into a different port."
        )
        if visible:
            seen = ", ".join(f"{d.serial} ({d.usb or 'no usb path'})" for d in visible)
            message += f" fastboot sees: {seen}."
        else:
            message += " fastboot sees no devices at all."
        raise FastbootError(message)

    async def present(self) -> bool:
        try:
            await self.resolve()
        except FastbootError:
            return False
        return True

    async def wait_present(self, timeout: float, interval: float = 1.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            if await self.present():
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(interval)

    async def getvar(self, name: str) -> str | None:
        """Return a bootloader variable, or ``None`` if unsupported or the call failed."""
        try:
            result = await self.run(["getvar", name], timeout=self.command_timeout)
        except FastbootError:
            return None
        return parse_getvar(name, result.output)

    async def is_userspace(self) -> bool:
        return (await self.getvar("is-userspace") or "").lower() == "yes"

    async def run(
        self,
        args: list[str],
        *,
        timeout: float | None = None,
        stall_timeout: float | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> Result:
        """Run a command against this device; raise ``FastbootError`` on failure.

        The serial is resolved from the bench port immediately before the command,
        so a device that is not in fastboot fails fast instead of leaving fastboot
        blocked in ``< waiting for device >``.
        """
        serial = await self.resolve()
        result = await self._exec(
            ["-s", serial, *args], timeout=timeout, stall_timeout=stall_timeout, on_output=on_output
        )
        if result.returncode != 0:
            raise FastbootError(
                f"fastboot {' '.join(args)} failed (rc={result.returncode}): {result.output.strip()[-2000:]}"
            )
        return result

    async def _exec(
        self,
        args: list[str],
        *,
        timeout: float | None = None,
        stall_timeout: float | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> Result:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            raise FastbootError(f"fastboot binary not found: {self.binary}") from None

        assert proc.stdout is not None
        chunks: list[str] = []
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while True:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise FastbootError(f"fastboot {' '.join(args)} timed out after {timeout}s")
                waits = [w for w in (stall_timeout, remaining) if w is not None]
                wait = min(waits) if waits else None
                try:
                    data = await asyncio.wait_for(proc.stdout.read(4096), wait)
                except TimeoutError:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise FastbootError(f"fastboot {' '.join(args)} timed out after {timeout}s") from None
                    raise FastbootStalled(
                        f"fastboot {' '.join(args)} made no progress for {stall_timeout}s"
                    ) from None
                if not data:
                    break
                text = data.decode(errors="replace")
                chunks.append(text)
                if on_output is not None:
                    on_output(text)
            returncode = await proc.wait()
        except BaseException:
            # Killing the *host* process is protocol-safe: the device either
            # discards a partial download or finishes a write it already accepted.
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            raise
        return Result(returncode=returncode, output="".join(chunks))
