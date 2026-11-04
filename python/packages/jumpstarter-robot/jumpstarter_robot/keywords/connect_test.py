"""Tests for `Connect To Device` dispatch, with fake clients matched by class name."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import cast

import pytest
from robot.api import Failure

from .. import context
from ..testing import use_client
from . import adb, connect, ios, ssh
from .connect import ConnectKeywords, class_names, family_of
from jumpstarter.client import DriverClient
from jumpstarter.client.base import StubDriverClient


@dataclass
class Node:
    children: dict = field(default_factory=dict)
    labels: dict = field(default_factory=dict)


class SSHWrapperClient(Node):
    pass


class AdbClient(Node):
    pass


class AdbDeviceClient(Node):
    pass


class AndroidEmulatorClient(Node):
    pass


class IosDeviceClient(Node):
    pass


class PowerClient(Node):
    pass


class CompositeClient(Node):
    pass


class FancySshClient(SSHWrapperClient):
    """A subclass is recognized through its MRO."""


@pytest.fixture
def calls(monkeypatch) -> list[tuple]:
    recorded: list[tuple] = []

    def recorder(family: str, result: int):
        def open_(path, alias=None, session=None, **options):
            recorded.append((family, path, alias, session, options))
            return result

        return open_

    monkeypatch.setattr(ssh, "open_ssh_connection", recorder("ssh", 1))
    monkeypatch.setattr(adb, "open_adb_device", recorder("adb", 2))
    monkeypatch.setattr(ios, "open_ios_device", recorder("ios", 3))
    return recorded


@pytest.fixture
def tree() -> Iterator[Node]:
    root = Node(
        children={
            "dut": CompositeClient(
                children={"ssh": SSHWrapperClient(children={"tcp": Node()}), "power": PowerClient()}
            ),
            "fancy": FancySshClient(),
            "server": AdbClient(),
            "phone": AdbDeviceClient(),
            "android": AndroidEmulatorClient(children={"adb": AdbClient(), "power": PowerClient()}),
            "iphone": IosDeviceClient(),
            "simulator": CompositeClient(children={"ios": IosDeviceClient(), "power": PowerClient()}),
            "both": CompositeClient(children={"ssh": SSHWrapperClient(), "adb": AdbDeviceClient()}),
            "fake": CompositeClient(children={"adb": PowerClient(), "ios": PowerClient()}),
            "power": PowerClient(),
        }
    )
    with use_client(cast(DriverClient, root)), use_client(cast(DriverClient, root), "other"):
        try:
            yield root
        finally:
            context.end_run()


@pytest.mark.parametrize(
    "path, family, target",
    [
        ("dut.ssh", "ssh", "dut.ssh"),
        ("dut", "ssh", "dut.ssh"),
        ("fancy", "ssh", "fancy"),
        ("server", "adb", "server"),
        ("phone", "adb", "phone"),
        ("android", "adb", "android"),
        ("android.adb", "adb", "android.adb"),
        ("iphone", "ios", "iphone"),
        ("simulator", "ios", "simulator.ios"),
        ("simulator.ios", "ios", "simulator.ios"),
        ("both", "adb", "both.adb"),
    ],
)
def test_dispatch(tree, calls, path, family, target):
    index = ConnectKeywords().connect_to_device(path)
    assert calls == [(family, target, None, None, {})]
    assert index == {"ssh": 1, "adb": 2, "ios": 3}[family]


def test_returns_the_alias_and_passes_options(tree, calls):
    result = ConnectKeywords().connect_to_device("android", alias="emu", session="other", timeout="5 min")
    assert result == "emu"
    assert calls == [("adb", "android", "emu", "other", {"timeout": "5 min"})]


def test_not_a_device(tree, calls):
    with pytest.raises(Failure, match=r"driver 'power' is .*PowerClient, which is not an SSH, ADB or iOS driver"):
        ConnectKeywords().connect_to_device("power")
    with pytest.raises(Failure, match="not an SSH, ADB or iOS driver"):
        ConnectKeywords().connect_to_device("fake")  # children with the right names but the wrong classes
    with pytest.raises(Failure, match="not present"):
        ConnectKeywords().connect_to_device("nothing")
    assert calls == []


def test_family_of():
    assert family_of(SSHWrapperClient()) == ("ssh", None)
    assert family_of(AndroidEmulatorClient(children={"adb": AdbClient()})) == ("adb", None)
    assert family_of(CompositeClient(children={"ios": IosDeviceClient()})) == ("ios", "ios")
    assert family_of(CompositeClient(children={"tcp": Node()})) is None
    assert family_of(PowerClient()) is None


class _Stub(StubDriverClient):
    def __del__(self):  # never connected, nothing to close
        pass


def test_stub_clients_are_recognized_by_their_label():
    stub = object.__new__(_Stub)
    object.__setattr__(stub, "labels", {"jumpstarter.dev/client": "jumpstarter_driver_adb.client.AdbClient"})
    object.__setattr__(stub, "children", {})
    assert "AdbClient" in class_names(stub)
    assert family_of(stub) == ("adb", None)


def test_keyword_is_exposed_by_the_jumpstarter_library():
    from ..library import Jumpstarter

    library = Jumpstarter()
    assert "connect_to_device" in library.get_keyword_names()
    assert library.connect_to_device.robot_name == "Connect To Device"


def test_child_path():
    assert connect.child_path("dut", None) == "dut"
    assert connect.child_path("dut", "ssh") == "dut.ssh"
    assert connect.child_path("", "ios") == "ios"
