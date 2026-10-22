import logging
from dataclasses import field
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from jumpstarter_driver_composite.driver import Composite, Proxy
from pydantic.dataclasses import dataclass

from .driver import PySerial
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

    def close(self):
        self.calls.append("close")
        super().close()


@dataclass(kw_only=True)
class NoCyclePower(Driver):
    """Power driver whose plain DriverClient provides no cycle()."""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"


@pytest.fixture(autouse=True)
def _no_power_cycle_wait(monkeypatch):
    """PowerClient.cycle() sleeps 2 s between off and on; skip it in tests."""
    monkeypatch.setattr("jumpstarter_driver_power.client.time.sleep", lambda _s: None)


def test_power_cycle_hook_without_power_child():
    with serve(PySerial(url="loop://")) as client:
        assert client._power_cycle_hook() is None


def test_power_cycle_hook_ignores_sibling_power():
    """Only a `power` child of the serial device counts, not a sibling."""
    root = Composite(children={"serial": PySerial(url="loop://"), "power": FakePower()})
    with serve(root) as client:
        assert client.children["serial"]._power_cycle_hook() is None


def test_power_cycle_hook_child_without_cycle(caplog):
    with (
        caplog.at_level(logging.WARNING, logger="jumpstarter_driver_pyserial.client"),
        serve(PySerial(url="loop://", children={"power": NoCyclePower()})) as client,
    ):
        assert client._power_cycle_hook() is None
    assert "no cycle() method" in caplog.text


def test_dut_power_cycle_calls_child_cycle():
    fake = FakePower()
    with serve(PySerial(url="loop://", children={"power": fake})) as client:
        client.dut_power_cycle()
        # assert before serve() teardown closes the power child (twice: once via
        # the client tree's close, once via the Session's shutdown)
        assert fake.calls == ["off", "on"]


def test_dut_power_cycle_without_power_child():
    with serve(PySerial(url="loop://")) as client, pytest.raises(RuntimeError, match="no 'power' child"):
        client.dut_power_cycle()


def test_power_cycle_hook_runs_off_the_event_loop():
    """The hook works via portal.call, i.e. through to_thread from the loop."""
    fake = FakePower()
    with serve(PySerial(url="loop://", children={"power": fake})) as client:
        hook = client._power_cycle_hook()
        assert hook is not None
        client.portal.call(hook)
        assert fake.calls == ["off", "on"]


def test_power_cycle_hook_follows_ref_child():
    """A `ref:` child resolves to the referenced power driver's client."""
    fake = FakePower()
    root = Composite(
        children={
            "serial": PySerial(url="loop://", children={"power": Proxy(ref="power")}),
            "power": fake,
        }
    )
    with serve(root) as client:
        hook = client.children["serial"]._power_cycle_hook()
        assert hook is not None
        client.portal.call(hook)
        assert fake.calls == ["off", "on"]


def test_power_cycle_hook_failure_does_not_raise():
    """A failed cycle warns instead of killing the console."""
    with serve(PySerial(url="loop://", children={"power": FakePower()})) as client:
        hook = client._power_cycle_hook()
        assert hook is not None
        with patch.object(client, "dut_power_cycle", side_effect=RuntimeError("PDU unreachable")):
            client.portal.call(hook)  # must not raise


def test_kick_does_not_close_power_child():
    """A remote close() kick must not power the DUT off; session teardown closes it."""
    fake = FakePower()
    with serve(PySerial(url="loop://", children={"power": fake})) as client:
        client.call("close")
        assert "close" not in fake.calls
    assert "close" in fake.calls


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


def test_console_observe_never_builds_power_cycle_hook():
    with serve(PySerial(url="loop://", children={"power": FakePower()})) as client:
        with (
            patch.object(client, "_power_cycle_hook") as hook,
            patch("jumpstarter_driver_pyserial.client.Console.run"),
        ):
            result = CliRunner().invoke(client.cli(), ["console", "--observe"])
        assert result.exit_code == 0
        hook.assert_not_called()
