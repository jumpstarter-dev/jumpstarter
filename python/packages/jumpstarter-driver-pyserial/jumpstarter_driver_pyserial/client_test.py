import logging
from dataclasses import field
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from jumpstarter_driver_composite.driver import Composite, Proxy
from pydantic.dataclasses import dataclass

from .driver import PySerial
from jumpstarter.client.base import StubDriverClient
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class FakePower(Driver):
    """Power driver whose client is the real PowerClient (off → sleep → on)."""

    calls: list[str] = field(default_factory=list)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_power.client.PowerClient"

    @export
    def off(self):
        self.calls.append("off")

    @export
    def on(self):
        self.calls.append("on")


@dataclass(kw_only=True)
class NoCyclePower(Driver):
    """Power driver whose plain DriverClient provides no cycle()."""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"


@dataclass(kw_only=True)
class UnavailablePower(Driver):
    """Power driver whose client cannot be imported, so the tree gets a StubDriverClient."""

    @classmethod
    def client(cls) -> str:
        return "nonexistent_pkg.client.MissingClient"


@pytest.fixture(autouse=True)
def _no_power_cycle_wait(monkeypatch):
    """PowerClient.cycle() sleeps 2 s between off and on; skip it in tests."""
    monkeypatch.setattr("jumpstarter_driver_power.client.time.sleep", lambda _s: None)


def test_power_child_absent():
    with serve(PySerial(url="loop://")) as client:
        assert client._power_child() is None


def test_power_child_ignores_sibling_power():
    """Only a `power` child of the serial device counts, not a sibling."""
    root = Composite(children={"serial": PySerial(url="loop://"), "power": FakePower()})
    with serve(root) as client:
        assert client.children["serial"]._power_child() is None


def test_power_child_without_cycle(caplog):
    with (
        caplog.at_level(logging.WARNING, logger="jumpstarter_driver_pyserial.client"),
        serve(PySerial(url="loop://", children={"power": NoCyclePower()})) as client,
    ):
        assert client._power_child() is None
    assert "no cycle() method" in caplog.text


def test_power_child_cycles_the_power_driver():
    fake = FakePower()
    with serve(PySerial(url="loop://", children={"power": fake})) as client:
        power = client._power_child()
        assert power is client.children["power"]
        power.cycle()
        assert fake.calls == ["off", "on"]


def test_power_child_follows_ref():
    """A `ref:` child resolves to the referenced power driver's client."""
    fake = FakePower()
    root = Composite(
        children={
            "serial": PySerial(url="loop://", children={"power": Proxy(ref="power")}),
            "power": fake,
        }
    )
    with serve(root) as client:
        power = client.children["serial"]._power_child()
        assert power is not None
        power.cycle()
        assert fake.calls == ["off", "on"]


def test_power_child_unavailable_client_is_ignored(caplog):
    """A power client that can't be imported (not installed, or rejected by the allow list)
    must disable the hotkey, not break the serial console."""
    with (
        caplog.at_level(logging.WARNING, logger="jumpstarter_driver_pyserial.client"),
        serve(PySerial(url="loop://", children={"power": UnavailablePower()})) as client,
    ):
        assert isinstance(client.children["power"], StubDriverClient)
        assert client._power_child() is None
    assert "unavailable" in caplog.text


def test_console_opens_with_unavailable_power_client():
    with serve(PySerial(url="loop://", children={"power": UnavailablePower()})) as client:
        with patch("jumpstarter_driver_pyserial.client.Console.run") as run:
            result = CliRunner().invoke(client.cli(), ["console"])
        assert result.exit_code == 0
        run.assert_called_once()
        assert "Power cycle" not in result.output


def test_console_banner_advertises_power_cycle():
    with serve(PySerial(url="loop://", children={"power": FakePower()})) as client:
        with patch("jumpstarter_driver_pyserial.client.Console.run"):
            result = CliRunner().invoke(client.cli(), ["console"])
        assert result.exit_code == 0
        assert "Power cycle: CTRL+] x 3 times" in result.output


def test_console_banner_without_power_child():
    with serve(PySerial(url="loop://")) as client:
        with patch("jumpstarter_driver_pyserial.client.Console.run"):
            result = CliRunner().invoke(client.cli(), ["console"])
        assert result.exit_code == 0
        assert "Power cycle" not in result.output


def test_console_observe_never_looks_up_power_child():
    with serve(PySerial(url="loop://", children={"power": FakePower()})) as client:
        with (
            patch.object(client, "_power_child") as power_child,
            patch("jumpstarter_driver_pyserial.client.Console.run"),
        ):
            result = CliRunner().invoke(client.cli(), ["console", "--observe"])
        assert result.exit_code == 0
        power_child.assert_not_called()
