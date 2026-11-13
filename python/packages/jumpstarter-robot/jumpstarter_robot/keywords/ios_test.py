"""Tests for the iOS keywords, with fake iOS driver clients and fake tools on PATH.

The fake ``IosDeviceClient`` serves a fixed loopback endpoint; the tools are
small shell scripts that print the environment they were given, so the tests
check what a real ``idevice*`` or ``idb`` would see.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
import robot
from robot.api import ExecutionResult, Failure

from .. import context
from ..testing import use_client
from . import ios
from .ios import IosKeywords
from jumpstarter.client import DriverClient

ENDPOINTS = {"usbmux": "127.0.0.1:27015", "idb": "127.0.0.1:10882"}

TOOL = """#!/bin/sh
echo "usbmux=$USBMUXD_SOCKET_ADDRESS idb=$IDB_COMPANION args=$*"
echo "to stderr" >&2
exit ${FAKE_RC:-0}
"""

IDB = """#!/bin/sh
echo "idb $* companion=$IDB_COMPANION" >> "$IDB_CALLS"
case "$1" in
  screenshot) printf 'PNG' > "$2" ;;
  log) echo "syslog line 1"; echo "syslog line 2"; exec /bin/sleep 30 ;;
  launch) [ "$2" = "com.missing" ] && { echo "no such app" >&2; exit 1; } ;;
esac
echo "ok $1"
"""


@dataclass
class HttpsEndpoint:
    url: str
    ca_file: Path
    metadata: dict


@dataclass
class Node:
    children: dict = field(default_factory=dict)
    labels: dict = field(default_factory=dict)
    events: list = field(default_factory=list)


class IosDeviceClient(Node):
    """Serves fixed loopback endpoints and records what was opened."""

    def __init__(self, protocols=("usbmux", "idb"), present=True, **kwargs):
        super().__init__(**kwargs)
        self.protocols = list(protocols)
        self.present = present

    def info(self) -> dict:
        return {"present": self.present, "protocols": self.protocols, "forward_ports": [8100]}

    @contextmanager
    def serve(self, protocol: str | None = None, *, port: int = 0) -> Generator[str]:
        if protocol not in self.protocols:
            raise ValueError(f"Unsupported protocol {protocol!r}; supported protocols: {', '.join(self.protocols)}")
        if not self.present:
            raise RuntimeError("The iOS target is not present")
        self.events.append(f"serve:{protocol}")
        try:
            yield ENDPOINTS[protocol]
        finally:
            self.events.append(f"close:{protocol}")

    @contextmanager
    def forward(self, port: int, *, local_port: int | None = None) -> Generator[str]:
        if port != 8100:
            raise ValueError(f"Port {port} is not configured on this exporter; configured ports: 8100")
        self.events.append(f"forward:{port}")
        try:
            yield f"127.0.0.1:{port if local_port is None else local_port}"
        finally:
            self.events.append(f"unforward:{port}")

    @contextmanager
    def https(self, *, port: int = 0) -> Generator[HttpsEndpoint]:
        self.events.append("https")
        try:
            yield HttpsEndpoint("https://127.0.0.1:8443", Path("/tmp/ca.pem"), {"service": "web"})
        finally:
            self.events.append("unhttps")


class IosSimulatorClient(Node):
    pass


@pytest.fixture
def tools(tmp_path, monkeypatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("fake-ios-tool", TOOL), ("idb", IDB)):
        script = bin_dir / name
        script.write_text(text)
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("IDB_CALLS", str(tmp_path / "idb-calls.txt"))
    monkeypatch.delenv("USBMUXD_SOCKET_ADDRESS", raising=False)
    monkeypatch.delenv("IDB_COMPANION", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def tree(tools) -> Iterator[Node]:
    device = IosDeviceClient(protocols=("usbmux",))
    simulator = IosSimulatorClient(children={"ios": IosDeviceClient(protocols=("idb",)), "power": Node()})
    root = Node(children={"iphone": device, "simulator": simulator, "absent": IosDeviceClient(present=False)})
    with use_client(cast(DriverClient, root)):
        try:
            yield root
        finally:
            context.end_run()
            ios._devices.empty_cache()


def idb_calls(tools: Path) -> list[str]:
    path = tools / "idb-calls.txt"
    return path.read_text().splitlines() if path.exists() else []


# ---- opening and the environment ----


def test_open_serves_the_native_protocol(tree):
    keywords = IosKeywords()
    assert keywords.open_ios_device("iphone", alias="phone") == 1
    assert tree.children["iphone"].events == ["serve:usbmux"]
    assert keywords.get_ios_environment() == {"USBMUXD_SOCKET_ADDRESS": ENDPOINTS["usbmux"]}
    keywords.close_ios_device("phone")
    assert tree.children["iphone"].events == ["serve:usbmux", "close:usbmux"]


def test_composite_uses_its_ios_child(tree):
    keywords = IosKeywords()
    keywords.open_ios_device("simulator")
    assert ios.lookup().path == "simulator.ios"
    assert keywords.get_ios_environment() == {"IDB_COMPANION": ENDPOINTS["idb"]}


def test_unsupported_protocol_and_absent_device(tree):
    keywords = IosKeywords()
    with pytest.raises(Failure, match="Unsupported protocol 'idb'"):
        keywords.open_ios_device("iphone", protocol="idb")
    with pytest.raises(Failure, match="'carrier' is not supported"):
        keywords.open_ios_device("iphone", protocol="carrier")
    with pytest.raises(Failure, match="not present"):
        keywords.open_ios_device("absent")
    with pytest.raises(Failure, match="not an iOS driver"):
        keywords.open_ios_device("simulator.power")


def test_nothing_open(tree):
    with pytest.raises(RuntimeError, match="No iOS device is open"):
        IosKeywords().get_ios_environment()


def test_switch_and_close(tree):
    keywords = IosKeywords()
    keywords.open_ios_device("iphone", alias="phone")
    keywords.open_ios_device("simulator", alias="sim")
    assert keywords.get_ios_environment() == {"IDB_COMPANION": ENDPOINTS["idb"]}
    assert keywords.get_ios_environment("phone") == {"USBMUXD_SOCKET_ADDRESS": ENDPOINTS["usbmux"]}
    assert keywords.switch_ios_device("phone") == 2
    assert ios.lookup().path == "iphone"
    keywords.close_ios_device("sim")
    with pytest.raises(Failure, match="was closed"):
        keywords.get_ios_environment("sim")
    assert ios.lookup().path == "iphone"


def test_scope_end_closes_the_endpoint(tree):
    IosKeywords().open_ios_device("iphone")
    context.end_run()
    assert tree.children["iphone"].events == ["serve:usbmux", "close:usbmux"]
    with pytest.raises(RuntimeError, match="No iOS device is open"):
        ios.lookup()


# ---- local tools ----


def test_run_tool_sees_the_environment(tree, monkeypatch):
    keywords = IosKeywords()
    keywords.open_ios_device("iphone")
    result = keywords.run_ios_tool("fake-ios-tool", "-k", "ProductVersion")
    assert result.rc == 0
    assert result.stdout == f"usbmux={ENDPOINTS['usbmux']} idb= args=-k ProductVersion"
    assert result.stderr == "to stderr"
    monkeypatch.setenv("FAKE_RC", "3")
    assert keywords.run_ios_tool("fake-ios-tool", expected_rc=3).rc == 3
    assert keywords.run_ios_tool("fake-ios-tool", expected_rc=None).rc == 3
    with pytest.raises(Failure, match="exited with 3, expected 0: to stderr"):
        keywords.run_ios_tool("fake-ios-tool")


def test_run_tool_picks_the_device_by_alias(tree):
    keywords = IosKeywords()
    keywords.open_ios_device("iphone", alias="phone")
    keywords.open_ios_device("simulator", alias="sim")
    assert "idb=127.0.0.1:10882" in keywords.run_ios_tool("fake-ios-tool", alias="sim").stdout
    assert f"usbmux={ENDPOINTS['usbmux']}" in keywords.run_ios_tool("fake-ios-tool", alias="phone").stdout


def test_run_tool_missing_and_timeout(tree):
    keywords = IosKeywords()
    keywords.open_ios_device("iphone")
    with pytest.raises(Failure, match="'no-such-tool' was not found on PATH"):
        keywords.run_ios_tool("no-such-tool")
    with pytest.raises(Failure, match="did not finish within"):
        keywords.run_ios_tool("/bin/sleep", "5", timeout="0.2s")


# ---- forwards, HTTPS and info ----


def test_forward_https_and_info(tree):
    keywords = IosKeywords()
    keywords.open_ios_device("iphone")
    assert keywords.ios_forward_port(8100) == "127.0.0.1:8100"
    assert keywords.ios_forward_port(8100, local_port=0) == "127.0.0.1:0"
    with pytest.raises(Failure, match="Port 22 is not configured"):
        keywords.ios_forward_port(22)
    https = keywords.get_ios_https_endpoint()
    assert https.url == "https://127.0.0.1:8443"
    assert https.ca_file == "/tmp/ca.pem"
    assert https.metadata.service == "web"
    info = keywords.ios_device_info()
    assert info.present is True and info.protocols == ["usbmux"]
    keywords.close_ios_device()
    events = tree.children["iphone"].events
    # forwards and HTTPS close before the transport, in reverse order
    assert events[-1] == "close:usbmux"
    assert {"unforward:8100", "unhttps"} <= set(events)


# ---- idb helpers ----


def test_idb_helpers(tree, tools, monkeypatch):
    embedded: list[Path] = []
    monkeypatch.setattr(ios.context, "embed_image", embedded.append)
    keywords = IosKeywords()
    keywords.open_ios_device("simulator")
    app = tools / "MyApp.app"
    app.mkdir()
    assert keywords.ios_install_app(str(app)).stdout == "ok install"
    keywords.ios_launch_app("com.example.MyApp", "--flag")
    keywords.ios_terminate_app("com.example.MyApp")
    with pytest.raises(Failure, match="no such app"):
        keywords.ios_launch_app("com.missing")
    shot = Path(keywords.ios_screenshot())
    assert shot.read_bytes() == b"PNG"
    assert embedded == [shot]
    companion = f"companion={ENDPOINTS['idb']}"
    assert idb_calls(tools)[:3] == [
        f"idb install {app} {companion}",
        f"idb launch com.example.MyApp --flag {companion}",
        f"idb terminate com.example.MyApp {companion}",
    ]
    with pytest.raises(Failure, match="does not exist"):
        keywords.ios_install_app(str(tools / "Missing.app"))


def test_syslog_collects_for_a_duration(tree):
    keywords = IosKeywords()
    keywords.open_ios_device("simulator")
    log = keywords.ios_syslog(duration="0.5s")
    assert "syslog line 1" in log and "syslog line 2" in log


def test_idb_helpers_need_idb(tree, monkeypatch):
    keywords = IosKeywords()
    keywords.open_ios_device("simulator")
    monkeypatch.setattr(ios.shutil, "which", lambda name: None)
    for call in (
        lambda: keywords.ios_install_app("x.app"),
        lambda: keywords.ios_launch_app("com.example"),
        lambda: keywords.ios_terminate_app("com.example"),
        lambda: keywords.ios_screenshot(),
        lambda: keywords.ios_syslog(),
    ):
        with pytest.raises(Failure, match="needs idb on PATH: install the client with `pip install fb-idb`"):
            call()


def test_idb_helpers_need_the_idb_protocol(tree):
    keywords = IosKeywords()
    keywords.open_ios_device("iphone")
    with pytest.raises(Failure, match="needs the idb protocol.*protocol=idb"):
        keywords.ios_launch_app("com.example")


def test_extension_is_registered():
    from ..extensions import extensions_for

    assert "Connect" in [e.method_words for e in extensions_for(IosDeviceClient)]
    assert "Connect" not in [e.method_words for e in extensions_for(IosSimulatorClient)]


# ---- a Robot suite with the fakes ----


SUITE = """
*** Settings ***
Library    jumpstarter_robot.Jumpstarter
Library    jumpstarter_robot.Driver    simulator.ios    client=jumpstarter_robot.keywords.ios_test.IosDeviceClient
...    prefix=Simulator Ios    AS    simios

*** Test Cases ***
Tool Against The Simulator
    ${index}=    Open iOS Device    simulator    alias=sim
    &{env}=    Get iOS Environment
    Should Be Equal    ${env.IDB_COMPANION}    127.0.0.1:10882
    ${r}=    Run iOS Tool    fake-ios-tool    describe
    Should Contain    ${r.stdout}    idb=127.0.0.1:10882 args=describe
    Close iOS Device    sim

Node Shortcut
    Simulator Ios Connect    alias=short
    ${r}=    Run iOS Tool    idb    describe
    Should Be Equal    ${r.stdout}    ok describe

Connect To Device Dispatches To iOS
    ${id}=    Connect To Device    iphone
    Should Be Equal As Integers    ${id}    3
    ${r}=    Run iOS Tool    fake-ios-tool
    Should Contain    ${r.stdout}    usbmux=127.0.0.1:27015
"""


def test_suite_with_fakes(tree, tools):
    suite = tools / "ios.robot"
    suite.write_text(SUITE)
    rc = robot.run(
        str(suite),
        outputdir=str(tools / "out"),
        output=str(tools / "out" / "output.xml"),
        report="NONE",
        log="NONE",
        console="none",
    )
    result = ExecutionResult(str(tools / "out" / "output.xml"))
    statuses = {test.name: (test.status, test.message) for test in result.suite.all_tests}
    assert rc == 0, statuses
