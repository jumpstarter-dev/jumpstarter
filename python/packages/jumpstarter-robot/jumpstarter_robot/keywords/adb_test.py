"""Tests for the ADB keywords, with a fake adbutils and fake ADB driver clients.

Both endpoint shapes are covered: an ADB server forwarded from the exporter
(``AdbClient``, androidemulator's ``adb`` child) and a device's adbd added to
the local ADB server (``AdbDeviceClient``). The fakes are matched by class
name, as the real clients are.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
import robot
from robot.api import ExecutionResult, Failure

from .. import context
from ..testing import use_client
from . import adb
from .adb import AdbKeywords
from jumpstarter.client import DriverClient

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


# ---- a fake adbutils ----


class AdbError(Exception):
    pass


AdbError.__module__ = "adbutils.errors"


@dataclass
class ShellResult:
    command: str
    returncode: int
    output: str = ""
    stdout: str = ""
    stderr: str = ""


class FakeSync:
    def __init__(self, device: FakeDevice):
        self.device = device

    def push(self, src: str, dst: str) -> None:
        self.device.files[dst] = Path(src).read_bytes()

    def pull(self, src: str, dst: str) -> int:
        if src not in self.device.files:
            raise AdbError(f"remote object '{src}' does not exist")
        Path(dst).write_bytes(self.device.files[src])
        return len(self.device.files[src])


class FakeDevice:
    """An adbutils device: shell commands, properties, files and installs."""

    def __init__(self, serial: str, booted_after: int = 0):
        self.serial = serial
        self.files: dict[str, bytes] = {}
        self.installed: list[tuple[str, list[str]]] = []
        self.commands: list[Any] = []
        self.boot_polls = 0
        self.booted_after = booted_after
        self.sync = FakeSync(self)
        self.log = [f"line {n}" for n in range(1, 6)]

    def getprop(self, name: str) -> str:
        if name == "sys.boot_completed":
            self.boot_polls += 1
            return "1" if self.boot_polls > self.booted_after else ""
        return {"ro.product.model": "Pixel Fake"}.get(name, "")

    def shell2(self, command: str, timeout: float | None = None, rstrip: bool = False) -> ShellResult:
        self.commands.append(("shell2", command, timeout))
        if command.startswith("exit "):
            return ShellResult(command, int(command.split()[1]), output="")
        if command == "boom":
            raise AdbError("device offline")
        output = f"ran {command}"
        return ShellResult(command, 0, output=output)

    def shell(self, command: Any, encoding: str | None = "utf-8", timeout: float | None = None) -> Any:
        self.commands.append(("shell", command))
        if command[:2] == ["logcat", "-d"]:
            lines = int(command[-1])
            return "\n".join(self.log[-lines:])
        if command == ["logcat", "-c"]:
            self.log.clear()
            return ""
        if command == ["screencap", "-p"]:
            assert encoding is None
            return PNG
        if command == "reboot":
            self.boot_polls = 0
            self.booted_after = 1
            return ""
        return ""

    def install_remote(self, remote: str, clean: bool = False, flags: list | None = None) -> None:
        assert remote in self.files
        self.installed.append((remote, list(flags or [])))
        if clean:
            del self.files[remote]

    def uninstall(self, package: str) -> str:
        return "Success" if package == "com.example.app" else "Failure [DELETE_FAILED_INTERNAL_ERROR]"

    def reboot(self) -> None:
        self.shell("reboot")


class FakeAdbClient:
    """adbutils.AdbClient: lists the devices of the server at (host, port)."""

    servers: ClassVar[dict[tuple[str, int], list[FakeDevice]]] = {}
    created: ClassVar[list[tuple[Any, Any]]] = []

    def __init__(self, host: str | None = None, port: int | None = None):
        self.host, self.port = host or "127.0.0.1", port or 5037
        FakeAdbClient.created.append((host, port))
        self.list_calls = 0

    def device_list(self) -> list[FakeDevice]:
        self.list_calls += 1
        devices = FakeAdbClient.servers.get((self.host, self.port), [])
        # the first listing comes back empty, as right after an emulator powers on
        return list(devices) if self.list_calls > 1 else []

    def device(self, serial: str | None = None) -> FakeDevice:
        for device in FakeAdbClient.servers.get((self.host, self.port), []):
            if device.serial == serial:
                return device
        raise AdbError(f"device '{serial}' not found")


@pytest.fixture
def fake_adbutils(monkeypatch) -> types.ModuleType:
    module = types.ModuleType("adbutils")
    vars(module).update(AdbClient=FakeAdbClient, AdbError=AdbError)
    monkeypatch.setitem(sys.modules, "adbutils", module)
    monkeypatch.setattr(FakeAdbClient, "servers", {})
    monkeypatch.setattr(FakeAdbClient, "created", [])
    monkeypatch.setattr(adb, "POLL_INTERVAL", 0.01)
    return module


# ---- fake driver clients, matched by class name ----


@dataclass
class Node:
    children: dict = field(default_factory=dict)
    labels: dict = field(default_factory=dict)
    events: list = field(default_factory=list)


class AdbClient(Node):
    """The exporter's ADB server, forwarded to a local port."""

    port = 15037

    @contextmanager
    def forward_adb(self, host: str = "127.0.0.1", port: int = 0) -> Generator[tuple[str, int]]:
        self.events.append("forward")
        try:
            yield ("127.0.0.1", self.port)
        finally:
            self.events.append("unforward")


class AdbDeviceClient(Node):
    """One device's adbd, added to the local ADB server."""

    address = "127.0.0.1:40001"

    @contextmanager
    def connect(self, *, adb: str = "adb", **kwargs) -> Generator[str]:
        self.events.append(f"connect:{adb}")
        try:
            yield self.address
        finally:
            self.events.append("disconnect")


class AndroidEmulatorClient(Node):
    pass


class PowerClient(Node):
    pass


@pytest.fixture
def tree(fake_adbutils) -> Iterator[Node]:
    server = AdbClient()
    device = AdbDeviceClient()
    emulator = AndroidEmulatorClient(children={"adb": AdbClient(), "power": PowerClient()})
    root = Node(children={"server": server, "device": device, "android": emulator, "power": PowerClient()})
    FakeAdbClient.servers[("127.0.0.1", AdbClient.port)] = [FakeDevice("emulator-5554")]
    FakeAdbClient.servers[("127.0.0.1", 5037)] = [FakeDevice(AdbDeviceClient.address)]
    with use_client(cast(DriverClient, root)):
        try:
            yield root
        finally:
            context.end_run()
            AdbKeywords().close_all_adb_devices()


def emulator_device() -> FakeDevice:
    return FakeAdbClient.servers[("127.0.0.1", AdbClient.port)][0]


# ---- opening ----


def test_server_endpoint_needs_no_adb_binary(tree, monkeypatch):
    monkeypatch.setattr(adb.shutil, "which", lambda name: None)
    keywords = AdbKeywords()
    assert keywords.open_adb_device("server", alias="srv") == 1
    assert tree.children["server"].events == ["forward"]
    assert ("127.0.0.1", AdbClient.port) in FakeAdbClient.created
    assert keywords.adb_get_property("ro.product.model") == "Pixel Fake"
    keywords.close_adb_device()
    assert tree.children["server"].events == ["forward", "unforward"]


def test_emulator_composite_uses_its_adb_child(tree):
    keywords = AdbKeywords()
    keywords.open_adb_device("android", timeout="5s")
    assert tree.children["android"].children["adb"].events == ["forward"]
    assert adb.current().path == "android.adb"
    assert adb.current().kind == "server"
    assert adb.current().serial == "emulator-5554"


def test_waits_for_boot(tree):
    emulator_device().booted_after = 3
    AdbKeywords().open_adb_device("server")
    assert emulator_device().boot_polls == 4


def test_without_boot_wait(tree):
    emulator_device().booted_after = 10**9
    AdbKeywords().open_adb_device("server", wait_for_boot=False)
    assert emulator_device().boot_polls == 0


def test_device_endpoint_uses_the_local_adb_server(tree, monkeypatch):
    monkeypatch.setattr(adb.shutil, "which", lambda name: "/opt/adb" if name == "adb" else None)
    keywords = AdbKeywords()
    keywords.open_adb_device("device")
    assert tree.children["device"].events == ["connect:/opt/adb"]
    assert FakeAdbClient.created[-1] == (None, None)  # the default local server
    assert adb.current().kind == "device"
    assert adb.current().serial == AdbDeviceClient.address
    assert keywords.adb_shell("id") == "ran id"
    keywords.close_adb_device()
    assert tree.children["device"].events == ["connect:/opt/adb", "disconnect"]


def test_missing_adbutils_fails_with_install_hint(tree, monkeypatch):
    monkeypatch.setitem(sys.modules, "adbutils", None)
    with pytest.raises(Failure, match=r"pip install jumpstarter-robot\[android\]"):
        AdbKeywords().open_adb_device("server")
    assert tree.children["server"].events == ["forward", "unforward"]


def test_not_an_adb_driver(tree):
    with pytest.raises(Failure, match="not an ADB driver"):
        AdbKeywords().open_adb_device("power")
    with pytest.raises(Failure, match="not present"):
        AdbKeywords().open_adb_device("missing")


def test_no_device_open(tree):
    with pytest.raises(RuntimeError, match="No ADB device is open"):
        AdbKeywords().adb_shell("id")


# ---- the cache ----


def test_switch_and_close(tree, monkeypatch):
    monkeypatch.setattr(adb.shutil, "which", lambda name: "/opt/adb")
    keywords = AdbKeywords()
    assert keywords.open_adb_device("server", alias="emu") == 1
    assert keywords.open_adb_device("device", alias="phone") == 2
    assert adb.current().kind == "device"
    assert keywords.switch_adb_device("emu") == 2
    assert adb.current().kind == "server"
    keywords.close_adb_device("phone")
    assert adb.current().kind == "server"  # closing another one keeps the current
    with pytest.raises(Failure, match="was closed"):
        keywords.switch_adb_device("phone")
    with pytest.raises(Failure, match="Non-existing index or alias"):
        keywords.switch_adb_device("nope")
    keywords.close_adb_device()
    with pytest.raises(RuntimeError, match="No ADB device is open"):
        adb.current()


def test_scope_end_closes_devices(tree):
    AdbKeywords().open_adb_device("server")
    context.end_run()
    assert tree.children["server"].events == ["forward", "unforward"]
    with pytest.raises(RuntimeError, match="No ADB device is open"):
        adb.current()


# ---- device keywords ----


def test_shell(tree):
    keywords = AdbKeywords()
    keywords.open_adb_device("server")
    assert keywords.adb_shell("getprop ro.serialno") == "ran getprop ro.serialno"
    keywords.adb_shell("exit 2", expected_rc=2)
    assert keywords.adb_shell("exit 5", expected_rc=None) == ""
    with pytest.raises(Failure, match="exited with 1, expected 0"):
        keywords.adb_shell("exit 1")
    keywords.adb_shell("sleep 1", timeout="30s")
    assert emulator_device().commands[-1] == ("shell2", "sleep 1", 30.0)
    with pytest.raises(Failure, match="ADB Shell: device offline"):
        keywords.adb_shell("boom")


def test_install_uninstall_push_pull(tree, tmp_path):
    keywords = AdbKeywords()
    keywords.open_adb_device("server")
    apk = tmp_path / "app-debug.apk"
    apk.write_bytes(b"APK")
    keywords.adb_install(str(apk))
    assert emulator_device().installed == [("/data/local/tmp/app-debug.apk", ["-r", "-t", "-g"])]
    keywords.adb_install(str(apk), replace=False, grant_permissions=False)
    assert emulator_device().installed[-1][1] == ["-t"]
    assert "/data/local/tmp/app-debug.apk" not in emulator_device().files  # cleaned up
    with pytest.raises(Failure, match="does not exist"):
        keywords.adb_install(str(tmp_path / "missing.apk"))

    keywords.adb_uninstall("com.example.app")
    with pytest.raises(Failure, match="DELETE_FAILED"):
        keywords.adb_uninstall("com.other")

    data = tmp_path / "data.json"
    data.write_text("{}")
    keywords.adb_push(str(data), "/sdcard/data.json")
    assert emulator_device().files["/sdcard/data.json"] == b"{}"
    pulled = keywords.adb_pull("/sdcard/data.json", str(tmp_path / "back" / "data.json"))
    assert Path(pulled).read_text() == "{}"
    with pytest.raises(Failure, match="does not exist"):
        keywords.adb_pull("/sdcard/missing", str(tmp_path / "x"))
    with pytest.raises(Failure, match="does not exist"):
        keywords.adb_push(str(tmp_path / "missing"), "/sdcard/x")


# ---- a Robot suite with the fakes ----


SUITE = """
*** Settings ***
Library    jumpstarter_robot.Jumpstarter
Library    jumpstarter_robot.Driver    android    client=jumpstarter_robot.keywords.adb_test.AndroidEmulatorClient
...    prefix=Emulator    AS    emulator

*** Test Cases ***
Shell And Properties
    ${index}=    Open ADB Device    android    alias=emu    timeout=10s
    Should Be Equal As Integers    ${index}    1
    ${out}=    ADB Shell    echo hi
    Should Be Equal    ${out}    ran echo hi
    ${model}=    ADB Get Property    ro.product.model
    Should Be Equal    ${model}    Pixel Fake
    Run Keyword And Expect Error    *exited with 3*    ADB Shell    exit 3
    ADB Shell    exit 3    expected_rc=3

Install And Close
    Open ADB Device    android    alias=second    scope=test
    Switch ADB Device    emu
    Close All ADB Devices
"""


def test_suite_with_fakes(tree, tmp_path):
    suite = tmp_path / "adb.robot"
    suite.write_text(SUITE)
    rc = robot.run(
        str(suite),
        outputdir=str(tmp_path),
        output=str(tmp_path / "output.xml"),
        report="NONE",
        log="NONE",
        console="none",
    )
    result = ExecutionResult(str(tmp_path / "output.xml"))
    statuses = {test.name: (test.status, test.message) for test in result.suite.all_tests}
    assert rc == 0, statuses
    assert {status for status, _ in statuses.values()} == {"PASS"}
