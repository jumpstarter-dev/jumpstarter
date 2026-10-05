"""Async wrapper around the Android platform-tools ``fastboot`` CLI.

Shared by the driver (enter, preflight) and the job runner (commit).

Device pinning follows ``jumpstarter_driver_adb.driver.AdbDevice``: a device
is configured by exactly one of ``usb_port`` (the bench USB port, preferred),
``serial``, or ``address`` (fastboot over TCP). For ``usb_port`` the serial is
looked up from ``fastboot devices -l`` on every command, and the command is
addressed with ``-s SERIAL``. Identity is the bench port, so hardware can be
swapped without a config change, and a re-enumeration that changes the serial
is picked up. Nothing here ever picks "the first device found".

A device on TCP (userspace ``fastbootd`` on a network) never appears in
``fastboot devices``, so ``address`` is used as the ``-s tcp:HOST:PORT``
selector as is, and presence is asked of the device itself: a ``getvar`` that
something speaking fastboot has to answer. A bare TCP connect would say
"present" for a forwarded port (``adb forward``) with nothing behind it.
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
DEFAULT_TCP_PORT = 5554  # fastboot's own default for tcp: selectors


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


def normalize_address(address: str) -> str:
    """Return ``address`` as the ``tcp:HOST:PORT`` selector ``fastboot -s`` takes.

    Accepts ``host``, ``host:port`` and ``[ipv6]:port``, each with or without a
    leading ``tcp:``. The port defaults to 5554, fastboot's default, and is
    always written out, so a device has one identity however it was configured.
    """
    text = str(address).strip()
    if text.startswith("udp:"):
        raise ValueError("'address' is TCP only (fastboot over UDP is not supported)")
    text = text.removeprefix("tcp:")
    if not text:
        raise ValueError("'address' must not be empty")

    if text.startswith("["):  # [ipv6] or [ipv6]:port
        inner, closed, rest = text[1:].partition("]")
        if not closed or not inner or (rest and not rest.startswith(":")):
            raise ValueError(f"'address' {address!r} is not [ipv6] or [ipv6]:port")
        host, has_port, port_text = f"[{inner}]", bool(rest), rest[1:]
    else:
        if text.count(":") > 1:
            raise ValueError(f"'address' {address!r} looks like an IPv6 address: put it in brackets, [::1]:5554")
        host, has_port, port_text = text.partition(":")[0], ":" in text, text.partition(":")[2]
    if not host:
        raise ValueError(f"'address' {address!r} has no host")
    if not has_port:
        return f"tcp:{host}:{DEFAULT_TCP_PORT}"
    if not port_text.isdigit() or not 0 < int(port_text) < 65536:
        raise ValueError(f"'address' {address!r} has an invalid port {port_text!r}")
    return f"tcp:{host}:{int(port_text)}"


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
    """One device, pinned by exactly one of ``usb_port`` (preferred), ``serial``, or ``address`` (TCP)."""

    usb_port: str | None = None
    serial: str | None = None
    address: str | None = None
    binary: str = "fastboot"
    command_timeout: float = 30.0
    probe_timeout: float = 5.0
    """How long to wait for a TCP ``address`` to answer when asking whether it is present."""

    def __post_init__(self):
        if sum(pin is not None for pin in (self.usb_port, self.serial, self.address)) != 1:
            raise ValueError(
                "exactly one of 'usb_port' (the bench USB port, preferred), 'serial' or 'address' "
                "(fastboot over TCP) is required"
            )
        if self.usb_port is not None:
            self.usb_port = normalize_usb_port(self.usb_port)
        elif self.address is not None:
            self.address = normalize_address(self.address)
        elif not str(self.serial).strip():
            raise ValueError("'serial' must not be empty")

    @property
    def label(self) -> str:
        """The bench identity of this device: its USB port, TCP address, or configured serial."""
        return self.usb_port or self.address or str(self.serial)

    async def list_devices(self) -> list[FastbootDevice]:
        result = await self._exec(["devices", "-l"], timeout=self.command_timeout)
        return parse_devices(result.output)

    async def resolve(self) -> str:
        """The serial to address this device by, resolved fresh on every call.

        For a TCP ``address`` that is the ``tcp:HOST:PORT`` selector, once the
        device has answered. Raises ``FastbootError`` if the device is not in
        fastboot right now.
        """
        if self.address is not None:
            await self._probe()
            return self.address
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

    async def _probe(self) -> None:
        """Ask the device at ``address`` for its protocol version; raise ``FastbootError`` if nothing answers.

        fastboot waits forever for a device that isn't there, printing why
        (``error: Failed to connect to ...: Connection refused``) and then
        ``< waiting for ... >``. The probe gives up at that line, so an absent
        device is reported at once with fastboot's own reason, and is otherwise
        bounded by ``probe_timeout`` (an address that never answers). Any
        answer counts, a bootloader that refuses the variable
        (``FAILED (remote: ...)``) included: it is there.
        """
        try:
            result = await self._exec(
                ["-s", str(self.address), "getvar", "version"],
                timeout=self.probe_timeout,
                give_up_on=lambda output: "waiting for" in output,
            )
            problem = None if result.returncode == 0 or "remote:" in result.output else result.output.strip()[-300:]
        except FastbootError as exc:
            problem = str(exc)
        if problem is not None:
            raise FastbootError(
                f"no fastboot device at {self.address} ({problem}). It may be powered off, booted normally rather "
                "than into fastboot (or fastbootd), or not reachable from the exporter: for a forwarded port, "
                "check the forward."
            )

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
        give_up_on: Callable[[str], bool] | None = None,
    ) -> Result:
        """Run ``fastboot args``; ``give_up_on(output so far)`` returning true abandons it with a ``FastbootError``."""
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
                if give_up_on is not None and give_up_on("".join(chunks)):
                    said = " ".join("".join(chunks).split())[-300:]
                    raise FastbootError(f"fastboot {' '.join(args)} is waiting for the device: {said}")
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
