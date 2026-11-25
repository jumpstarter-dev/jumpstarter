"""The Bluetooth of an Android device as the device's own stack has it, asked over ADB.

Nothing here is on the air or between two devices: it is what a person would read in the device's settings,
and do there. It is the same for a virtual device and a physical one, as long as ADB reaches it.
"""

from __future__ import annotations

import base64
import os
import re
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field

import anyio

from . import identity
from .capture import NotBtsnoop, Record, parse_btsnoop, select
from .interface import BluetoothDriver
from jumpstarter.driver import export

# The questions of `identity`, and two more: who is in front (an Android Automotive device runs its system
# as user 0 and its person as another), and how the stack records its HCI.
SNOOP_MODE = "persist.bluetooth.btsnooplogmode"
QUESTIONS = (*identity.QUESTIONS, "am get-current-user", f"getprop {SNOOP_MODE}")
SCRIPT = f"; echo {identity.MARK}; ".join(QUESTIONS)

MANAGER = "cmd bluetooth_manager"
CONNECTION = re.compile(r"(?m)^\s*ConnectionState:\s*(\w+)\s*$")
DISCOVERING = re.compile(r"(?m)^\s*Discovering:\s*(true|false)\s*$")
BONDED = re.compile(r"(?m)^\s*Bonded devices:\s*$")
# `    XX:XX:XX:XX:AB:CD [BR/EDR][ 0x5A020C ] Pixel 9`, as AdapterProperties prints a bonded device. Its
# address is given with only its last two bytes. A device known under two addresses has ` => ` and the second.
BOND = re.compile(
    r"^\s+(?P<address>(?:[0-9A-Fa-fX]{2}:){5}[0-9A-Fa-fX]{2})(?:\s*=>\s*(?P<identity>(?:[0-9A-Fa-fX]{2}:){5}[0-9A-Fa-fX]{2}))?"
    r"\s*\[(?P<type>[^\]]*)\]\[\s*0x(?P<class>[0-9A-Fa-f]+)\s*\]\s?(?P<name>.*)$"
)
CHUNK = 48 * 1024


def parse_state(output: str) -> dict:
    """What a device said of its Bluetooth, in the shape of ``get_bluetooth_state``. What it did not say is
    None: a Bluetooth that is off has no adapter to list bonds."""
    answers = [answer.strip() for answer in output.split(identity.MARK)]
    answers += [""] * (len(QUESTIONS) - len(answers))
    manager, user, snoop = answers[3], answers[4], answers[5]
    reading = identity.parse(output)
    state = identity.STATE.search(manager)
    connection, discovering = CONNECTION.search(manager), DISCOVERING.search(manager)
    adapter = {
        "enabled": reading.on,
        "state": state.group(1).upper().removeprefix("STATE_") if state else None,
        "name": reading.name or None,
        "address": reading.address or None,
        "class_of_device": reading.class_of_device,
        "connection_state": connection.group(1).removeprefix("STATE_") if connection else None,
        "discovering": discovering.group(1) == "true" if discovering else None,
        # The adapter is the system user's (0); this is who is in front, which on a car is somebody else.
        "android_user": int(user) if user.isdigit() else None,
        "snoop_log_mode": snoop or None,
    }
    bonds: list[dict] | None = None
    if listed := BONDED.search(manager):
        bonds = []
        for line in manager[listed.end() :].splitlines()[1:]:
            bond = BOND.match(line)
            if not bond:
                if line.strip():
                    break
                continue
            bonds.append(
                {
                    "address": bond["address"].upper(),
                    **({"identity_address": bond["identity"].upper()} if bond["identity"] else {}),
                    "type": bond["type"].strip(),
                    "class_of_device": int(bond["class"], 16),
                    "name": bond["name"].strip(),
                }
            )
    # The stack says whether it has a link, not to whom.
    connections = [] if adapter["connection_state"] == "DISCONNECTED" else None
    return {"adapter": adapter, "connections": connections, "bonds": bonds}


@dataclass(kw_only=True)
class AndroidBluetooth(BluetoothDriver):
    """The Bluetooth of an Android device, as its own stack has it: asked, switched and recorded with ``adb``.

    It has no port and joins nothing. It says what the device's adapter is (`get_bluetooth_state`), switches
    it (`set_enabled`), resets it (`factory_reset`) and records its HCI with Android's own snoop log
    (`start_capture`), which needs a ``userdebug`` or ``eng`` build: the log is read, and its mode set, as root.
    """

    # The device as the ADB server knows it: `host:port` for one reached over TCP.
    adb: str
    adb_path: str = "adb"
    # The ADB server to ask. 15037 is the one the `AdbDevice` driver of the same exporter runs.
    server_port: int = 15037
    timeout: float = 30.0
    # Where the device's stack writes its snoop log. This is where AOSP's does
    # (`os::ParameterProvider::SnoopLogFilePath`); the log of the run before is beside it, with `.last`.
    snoop_log: str = "/data/misc/bluetooth/logs/btsnoop_hci.log"
    # Seconds between looks at the log while a test waits for an event.
    poll: float = 1.0

    CAPABILITIES = ("state", "enable", "bonds", "capture", "packets")

    _run: Callable[[list[str]], Awaitable[bytes]] = field(init=False, repr=False)
    _error: str = field(init=False, default="", repr=False)
    _capturing: bool = field(init=False, default=False, repr=False)
    # The mode the log had before `start_capture` changed it. None: it was not changed.
    _mode_before: str | None = field(init=False, default=None, repr=False)
    # Where the recording starts in the log: the time of the log's first record, and how many it had.
    _mark: tuple[float, int] = field(init=False, default=(0.0, 0), repr=False)
    _kept: bytes | None = field(init=False, default=None, repr=False)

    def __post_init__(self):
        super().__post_init__()
        self._run = self._adb

    async def _adb(self, arguments: list[str]) -> bytes:
        environment = {**os.environ, "ANDROID_ADB_SERVER_PORT": str(self.server_port)}
        with anyio.fail_after(self.timeout):
            done = await anyio.run_process([self.adb_path, *arguments], env=environment, check=False)
        if done.returncode != 0:
            said = (done.stderr or done.stdout or b"").decode(errors="replace").strip().splitlines()
            raise RuntimeError(said[-1] if said else f"adb exited with {done.returncode}")
        return done.stdout

    async def _shell(self, script: str, *, root: bool = False) -> str:
        # (`su 0` is there on a userdebug or eng build only: the builds a snoop log can be read on)
        command = f"su 0 sh -c '{script}'" if root else script
        try:
            return (await self._run(["-s", self.adb, "shell", command])).decode(errors="replace")
        except Exception:
            if ":" in self.adb:
                # A device reached over TCP is not known to the server until something has connected to it.
                await self._run(["connect", self.adb])
            raise

    async def _ask(self) -> dict:
        try:
            state = parse_state(await self._shell(SCRIPT))
        except Exception as error:  # (the device is off, booting, or not there)
            self._error = str(error) or type(error).__name__
            raise
        self._error = ""
        return state

    @export
    async def status(self) -> dict:
        """``attached``: the device answered the last time it was asked. There is no far end: ``peer`` is
        always false. ``capturing``: its HCI is being recorded. ``last_error``: why it did not answer."""
        return {
            "running": True,
            "attached": not self._error,
            "peer": False,
            "capturing": self._capturing,
            "last_error": self._error,
        }

    @export
    async def get_bluetooth_state(self) -> dict:
        """The device is asked now. ``adapter`` also has ``connection_state`` (whether the adapter has a link:
        the stack does not list them), ``discovering``, ``android_user`` (who is in front: on Android
        Automotive that is not the system user the adapter belongs to) and ``snoop_log_mode``. ``bonds`` have
        ``name``, ``class_of_device``, ``type`` and an ``address`` of which Android gives the last two bytes.
        A device that does not answer has ``error``, and nothing else is known of it."""
        state: dict = {"capabilities": self.capabilities(), "adapter": None, "connections": None, "bonds": None}
        try:
            state.update(await self._ask())
        except Exception as error:  # noqa: BLE001 - said in the state, as everything else of the device is
            state["error"] = str(error) or type(error).__name__
        return {**state, "link": None}

    async def _switch(self, on: bool) -> None:
        state = "STATE_ON" if on else "STATE_OFF"
        await self._shell(f"{MANAGER} {'enable' if on else 'disable'}; {MANAGER} wait-for-state:{state}")

    @export
    async def set_enabled(self, on: bool = True) -> dict:
        """Switch the device's Bluetooth on or off, as its settings would, and wait until it is."""
        await self._switch(on)
        return await self.get_bluetooth_state()

    @export
    async def factory_reset(self) -> dict:
        """Reset the device's Bluetooth to what it was when new, and switch it on again: every bond is
        forgotten, and with them whatever else the stack had kept. There is no forgetting one bond from a
        shell: that is done in the device's settings.

        This runs ``cmd bluetooth_manager factoryReset`` as root (a ``userdebug`` or ``eng`` build). The
        command is not in the AOSP sources of Android 14 to 16; a device whose Bluetooth does not have it
        says so, and is left as it was."""
        try:
            await self._shell(f"{MANAGER} factoryReset", root=True)
        except Exception as error:
            raise RuntimeError(
                f"the device's Bluetooth was not reset ('{MANAGER} factoryReset', as root): {error}"
            ) from error
        await self._switch(True)
        return await self.get_bluetooth_state()

    # The snoop log.

    async def _mode(self) -> str:
        return (await self._shell(f"getprop {SNOOP_MODE}")).strip()

    async def _set_mode(self, mode: str) -> None:
        """The stack reads the mode when it starts, so it is started again: every link it has is dropped."""
        await self._shell(f'setprop {SNOOP_MODE} "{mode}"', root=True)
        await self._switch(False)
        await self._switch(True)

    async def _pull(self, path: str) -> bytes:
        return await self._run(["-s", self.adb, "exec-out", f"su 0 cat {path} 2>/dev/null"])

    async def _log(self, last: bool = False) -> bytes:
        if last:
            return await self._pull(self.snoop_log + ".last")
        if self._kept is not None and not self._capturing:
            return self._kept
        return await self._pull(self.snoop_log)

    @staticmethod
    def _records(data: bytes) -> list[Record]:
        try:
            return parse_btsnoop(data)
        except NotBtsnoop:
            return []  # (no log yet: a stack that has just started has written nothing)

    @export
    async def start_capture(self) -> dict:
        """Record the device's HCI with Android's own snoop log, from now on.

        The log's mode (``persist.bluetooth.btsnooplogmode``) has to be ``full``, and the stack reads it only
        when it starts. If it is anything else it is set, and **the device's Bluetooth is switched off and
        on**: its links are dropped, its bonds stay. A device that is already in that mode is left alone, and
        the recording starts at what its log has now."""
        before = await self._mode()
        if before != "full":
            await self._set_mode("full")
            self._mode_before = before
        records = self._records(await self._pull(self.snoop_log))
        self._mark = (records[0].time, len(records)) if records else (0.0, 0)
        self._kept, self._capturing = None, True
        return await self.capture_status()

    @export
    async def stop_capture(self, restore: bool = False) -> dict:
        """Take the log as it is now, and keep it for `read_capture` and `read_packets`.

        The mode stays ``full``: putting it back restarts the device's Bluetooth once more, and a stack that
        starts with its log disabled deletes the log. ``restore`` does put it back, after the log was taken."""
        if self._capturing:
            self._kept = await self._pull(self.snoop_log)
            self._capturing = False
        if restore and self._mode_before is not None:
            await self._set_mode(self._mode_before or "disabled")
            self._mode_before = None
        return await self.capture_status()

    @export
    async def capture_status(self) -> dict:
        """``capturing``; ``mode``, as the device has it now; ``changed_mode``: `start_capture` set it (and
        what it was is ``mode_before``); ``path`` of the log on the device; ``kept``: the bytes taken by
        `stop_capture`."""
        return {
            "capturing": self._capturing,
            "mode": await self._mode(),
            "changed_mode": self._mode_before is not None,
            "mode_before": self._mode_before or "",
            "path": self.snoop_log,
            "kept": len(self._kept) if self._kept is not None else 0,
        }

    @export
    async def read_capture(self, last: bool = False) -> AsyncGenerator[str, None]:
        """The snoop log as it is (btsnoop version 1, which Wireshark opens), in base64 chunks: the one
        `stop_capture` took, or the device's own while it is recording. It is the whole log, from when the
        device's Bluetooth last started, and it has the keys in it. ``last``: the log of the run before."""
        data = await self._log(last)
        if not data:
            raise RuntimeError(
                f"the device has no snoop log at {self.snoop_log}{'.last' if last else ''}: it is written in "
                "mode 'full' (start_capture), and read as root (a userdebug or eng build)"
            )
        for at in range(0, len(data), CHUNK):
            yield base64.b64encode(data[at : at + CHUNK]).decode()

    async def _packets(self, event: str, match: dict | None, since: int) -> list[dict]:
        records = self._records(await self._log())
        # A log that does not start where it did is a new one (the stack was restarted): all of it is new.
        start = self._mark[1] if records and records[0].time == self._mark[0] else 0
        return select(records, event, match, max(since, start))

    @export
    async def read_packets(self, event: str = "", match: dict | None = None, since: int = 0) -> list[dict]:
        """The packets recorded since `start_capture`, decoded: ``index`` (its place in the log), ``time``,
        ``direction``, ``type``, ``name`` and ``fields``. Link keys, PIN codes and the like are
        ``<redacted>``, and what is said above the link (ACL) is given by its length only."""
        return await self._packets(event, match, since)

    @export
    async def wait_for_event(
        self, event: str, timeout: float = 30.0, match: dict | None = None, since: int = 0
    ) -> dict:
        """The first packet of that name and with those fields, looked for in the log until it is there.
        The stack writes its log in its own time: allow a second or two."""
        deadline = time.monotonic() + timeout
        while True:
            if found := await self._packets(event, match, since):
                return found[0]
            if time.monotonic() >= deadline:
                return {}
            await anyio.sleep(min(self.poll, max(0.0, deadline - time.monotonic())))

    @export
    async def should_not_have_event(
        self, event: str, within: float = 0.0, match: dict | None = None, since: int = 0
    ) -> dict:
        """The first packet of that name and with those fields, if there is one in the log now or ``within``
        seconds from now. Empty: there was none."""
        return await self.wait_for_event(event, within, match, since)
