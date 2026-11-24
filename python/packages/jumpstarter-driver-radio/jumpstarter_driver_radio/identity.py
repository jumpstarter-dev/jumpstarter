"""Who a device is on the air, asked of the device itself.

A Bluetooth half has to say what the device beside it is: its name, its class and its own address. An Android
device says so over ADB, and goes on saying so: it is asked again every few seconds, so that a rename, or
Bluetooth switched off and on, is noticed without a test.
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import anyio

# One shell command for all of it, cut apart at the marks. The commands are those a person would type:
# the address and the name the device has stored, its class as the system property gives it, and the Bluetooth
# manager's own account of itself, which has the name the adapter really has and says whether it is on.
MARK = "--jumpstarter-radio--"
QUESTIONS = (
    "settings --user 0 get secure bluetooth_address",  # (the adapter belongs to the system user)
    "getprop bluetooth.device.class_of_device",
    "settings --user 0 get secure bluetooth_name",
    "dumpsys bluetooth_manager",
)
SCRIPT = f"; echo {MARK}; ".join(QUESTIONS)

ADDRESS = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
NAME = re.compile(r"(?m)^\s*Name:\s*(.+?)\s*$")
STATE = re.compile(r"(?mi)^\s*state:\s*(\w+)\s*$")
ENABLED = re.compile(r"(?mi)^\s*enabled:\s*(true|false)\s*$")


@dataclass(frozen=True)
class Reading:
    """What a device said of its Bluetooth. What it did not say is empty (or None)."""

    name: str = ""
    address: str = ""
    class_of_device: int | None = None
    on: bool | None = None  # its Bluetooth is switched on


def class_of_device(text: str) -> int | None:
    """The system property is the three bytes of the class, in decimal, most significant first: "90,2,12"."""
    try:
        parts = bytes(int(part) for part in text.strip().split(","))
    except ValueError:
        return None
    return int.from_bytes(parts, "big") if len(parts) == 3 else None


def parse(output: str) -> Reading:
    answers = [answer.strip() for answer in output.split(MARK)]
    answers += [""] * (len(QUESTIONS) - len(answers))
    address, device_class, stored_name, manager = answers[:4]
    name = NAME.search(manager)
    state, enabled = STATE.search(manager), ENABLED.search(manager)
    on: bool | None = None
    if state:
        on = state.group(1).upper() in ("ON", "STATE_ON")
    elif enabled:
        on = enabled.group(1).lower() == "true"
    return Reading(
        # the name the adapter has now; what is stored is the name it will have when it is on
        name=name.group(1) if name else ("" if stored_name in ("", "null") else stored_name),
        address=address.upper() if ADDRESS.match(address) else "",
        class_of_device=class_of_device(device_class),
        on=on,
    )


class AdbIdentity:
    """An Android device's Bluetooth identity, read with ``adb shell``."""

    kind = "adb"

    def __init__(
        self,
        serial: str,
        *,
        adb_path: str = "adb",
        server_port: int = 15037,
        timeout: float = 15.0,
        run: Callable[[list[str]], Awaitable[str]] | None = None,
    ) -> None:
        # The device as the ADB server knows it: `host:port` for one reached over TCP.
        self.serial = serial
        self.adb_path = adb_path
        # The ADB server to ask. 15037 is the one the `AdbDevice` driver of the same exporter runs.
        self.server_port = server_port
        self.timeout = timeout
        self._run = run or self._adb

    async def _adb(self, arguments: list[str]) -> str:
        environment = {**os.environ, "ANDROID_ADB_SERVER_PORT": str(self.server_port)}
        with anyio.fail_after(self.timeout):
            done = await anyio.run_process([self.adb_path, *arguments], env=environment, check=False)
        if done.returncode != 0:
            said = (done.stderr or done.stdout or b"").decode(errors="replace").strip().splitlines()
            raise RuntimeError(said[-1] if said else f"adb exited with {done.returncode}")
        return done.stdout.decode(errors="replace")

    async def read(self) -> Reading:
        try:
            return parse(await self._run(["-s", self.serial, "shell", SCRIPT]))
        except Exception:
            if ":" in self.serial:
                # A device reached over TCP is not known to the server until something has connected to it.
                await self._run(["connect", self.serial])
            raise


def source_from_config(config: dict | None) -> AdbIdentity | None:
    """The identity source an exporter's configuration names, if it names one."""
    if not config:
        return None
    options = dict(config)
    serial = options.pop("adb", None)
    if not serial:
        raise ValueError("identity: the only source there is is 'adb', the device's serial or host:port")
    known = {"adb_path", "server_port", "timeout"}
    if unknown := set(options) - known - {"interval"}:
        raise ValueError(f"identity: {', '.join(sorted(unknown))} not understood")
    return AdbIdentity(str(serial), **{key: value for key, value in options.items() if key in known})
