from dataclasses import field
from unittest.mock import AsyncMock, patch

from pydantic.dataclasses import dataclass

from .driver import PySerial
from jumpstarter.client.core import DriverMethodNotImplemented
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class FakePower(Driver):
    calls: list[str] = field(default_factory=list)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"

    @export
    def off(self):
        self.calls.append("off")

    @export
    def on(self):
        self.calls.append("on")

    @export
    def cycle(self):
        self.calls.append("cycle")


@dataclass(kw_only=True)
class FakeDut(Driver):
    """Minimal parent driver so the serial device has sibling drivers."""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"


def test_power_cycle_hook_resolves():
    dut = FakeDut(children={"serial": PySerial(url="loop://"), "power": FakePower()})
    with serve(dut) as client:
        serial = client.children["serial"]
        assert callable(serial._power_cycle_hook())


def test_power_cycle_hook_default_sequence():
    """The default sequence is off → sleep → on (ridesx/cuttlefish convention)."""
    fake = FakePower()
    dut = FakeDut(children={"serial": PySerial(url="loop://"), "power": fake})
    with serve(dut) as client:
        hook = client.children["serial"]._power_cycle_hook()
        assert hook is not None
        client.portal.call(hook)
        assert fake.calls == ["off", "on"]


def test_power_cycle_hook_executes_steps():
    fake = FakePower()
    dut = FakeDut(
        children={
            "serial": PySerial(url="loop://", power_control_method=["off", "sleep:0.01", "on"]),
            "power": fake,
        }
    )
    with serve(dut) as client:
        hook = client.children["serial"]._power_cycle_hook()
        assert hook is not None
        client.portal.call(hook)
        assert fake.calls == ["off", "on"]


def test_dut_power_cycle_remote_call():
    """dut_power_cycle is a regular driver call, usable outside the hotkey."""
    fake = FakePower()
    dut = FakeDut(
        children={"serial": PySerial(url="loop://", power_control_method=["cycle"]), "power": fake}
    )
    with serve(dut) as client:
        client.children["serial"].call("dut_power_cycle")
        assert fake.calls == ["cycle"]


def test_power_cycle_hook_ref_override():
    fake = FakePower()
    dut = FakeDut(
        children={
            "serial": PySerial(url="loop://", power_control_ref="psu", power_control_method=["cycle"]),
            "psu": fake,
        }
    )
    with serve(dut) as client:
        hook = client.children["serial"]._power_cycle_hook()
        assert hook is not None
        client.portal.call(hook)
        assert fake.calls == ["cycle"]


def test_power_cycle_hook_ref_missing():
    dut = FakeDut(children={"serial": PySerial(url="loop://", power_control_ref="main"), "psu": FakePower()})
    with serve(dut) as client:
        assert client.children["serial"]._power_cycle_hook() is None


def test_power_cycle_hook_no_power():
    with serve(FakeDut(children={"serial": PySerial(url="loop://")})) as client:
        assert client.children["serial"]._power_cycle_hook() is None


def test_power_cycle_hook_child_fallback():
    """Standalone serial (no parent) resolves the power driver among its children."""
    fake = FakePower()
    with serve(PySerial(url="loop://", children={"power": fake}, power_control_method=["cycle"])) as client:
        hook = client._power_cycle_hook()
        assert hook is not None
        client.portal.call(hook)
        assert fake.calls == ["cycle"]


def test_power_cycle_hook_sibling_preferred_over_child():
    """When both a sibling and a child match the ref, the sibling wins."""
    sibling = FakePower()
    child = FakePower()
    dut = FakeDut(
        children={
            "serial": PySerial(url="loop://", children={"power": child}, power_control_method=["cycle"]),
            "power": sibling,
        }
    )
    with serve(dut) as client:
        hook = client.children["serial"]._power_cycle_hook()
        assert hook is not None
        client.portal.call(hook)
        assert sibling.calls == ["cycle"]
        assert child.calls == []


def test_power_cycle_hook_disabled_via_empty_list():
    dut = FakeDut(children={"serial": PySerial(url="loop://", power_control_method=[]), "power": FakePower()})
    with serve(dut) as client:
        assert client.children["serial"]._power_cycle_hook() is None


def test_power_cycle_hook_disabled_via_none():
    dut = FakeDut(children={"serial": PySerial(url="loop://", power_control_method=None), "power": FakePower()})
    with serve(dut) as client:
        assert client.children["serial"]._power_cycle_hook() is None


def test_power_cycle_hook_invalid_method():
    dut = FakeDut(
        children={
            "serial": PySerial(url="loop://", power_control_method=["nonexistent_method"]),
            "power": FakePower(),
        }
    )
    with serve(dut) as client:
        assert client.children["serial"]._power_cycle_hook() is None


def test_power_cycle_hook_invalid_sleep():
    dut = FakeDut(
        children={
            "serial": PySerial(url="loop://", power_control_method=["sleep:bad"]),
            "power": FakePower(),
        }
    )
    with serve(dut) as client:
        assert client.children["serial"]._power_cycle_hook() is None


def test_power_cycle_hook_string_method():
    fake = FakePower()
    dut = FakeDut(
        children={"serial": PySerial(url="loop://", power_control_method="cycle"), "power": fake}
    )
    with serve(dut) as client:
        hook = client.children["serial"]._power_cycle_hook()
        assert hook is not None
        client.portal.call(hook)
        assert fake.calls == ["cycle"]


def test_power_cycle_hook_old_exporter():
    dut = FakeDut(children={"serial": PySerial(url="loop://"), "power": FakePower()})
    with serve(dut) as client:
        serial = client.children["serial"]
        with patch.object(serial, "call", side_effect=DriverMethodNotImplemented("power_control_config")):
            assert serial._power_cycle_hook() is None


def test_power_cycle_hook_failure_does_not_raise():
    """A failed dut_power_cycle call warns instead of killing the console."""
    dut = FakeDut(children={"serial": PySerial(url="loop://"), "power": FakePower()})
    with serve(dut) as client:
        serial = client.children["serial"]
        hook = serial._power_cycle_hook()
        assert hook is not None
        with patch.object(serial, "call_async", AsyncMock(side_effect=RuntimeError("PDU unreachable"))):
            client.portal.call(hook)  # must not raise
