import threading
from unittest.mock import MagicMock

from .driver import PySerial
from jumpstarter.common.utils import serve


def test_find_power_client_no_root():
    with serve(PySerial(url="loop://", power_control_method=["cycle"])) as client:
        # No root attribute set → should return None
        assert client._find_power_client() is None


def test_find_power_client_auto_discover():
    power = MagicMock(spec=["cycle", "children", "labels"])
    power.children = {}
    power.labels = {"jumpstarter.dev/client": "jumpstarter_driver_power.client.PowerClient"}
    root = MagicMock(spec=["children", "labels"])
    root.children = {"power": power}
    root.labels = {}

    with serve(PySerial(url="loop://")) as client:
        object.__setattr__(client, "root", root)
        assert client._find_power_client() is power


def test_make_power_cycle_calls_cycle():
    called = threading.Event()
    power = MagicMock()
    power.cycle = MagicMock(side_effect=lambda: called.set())

    with serve(PySerial(url="loop://")) as client:
        cycle_fn = client._make_power_cycle(power)
        client.portal.call(cycle_fn)
        assert called.is_set()


def test_find_power_client_ambiguous():
    power1 = MagicMock(spec=["children", "labels"])
    power1.children = {}
    power1.labels = {
        "jumpstarter.dev/client": "jumpstarter_driver_power.client.PowerClient",
        "jumpstarter.dev/name": "power1",
    }
    power2 = MagicMock(spec=["children", "labels"])
    power2.children = {}
    power2.labels = {
        "jumpstarter.dev/client": "jumpstarter_driver_power.client.PowerClient",
        "jumpstarter.dev/name": "power2",
    }
    root = MagicMock(spec=["children", "labels"])
    root.children = {"power1": power1, "power2": power2}
    root.labels = {}

    with serve(PySerial(url="loop://")) as client:
        object.__setattr__(client, "root", root)
        assert client._find_power_client() is None


def test_find_power_client_explicit_ref():
    power = MagicMock(spec=["children", "labels"])
    power.children = {}
    power.labels = {"jumpstarter.dev/client": "jumpstarter_driver_power.client.PowerClient"}
    other = MagicMock(spec=["children", "labels"])
    other.children = {}
    other.labels = {"jumpstarter.dev/client": "other.driver.Client"}
    root = MagicMock(spec=["children", "labels"])
    root.children = {"power": power, "other": other}
    root.labels = {}

    with serve(PySerial(url="loop://", power_control_ref="power")) as client:
        object.__setattr__(client, "root", root)
        assert client._find_power_client() is power


def test_find_power_client_explicit_ref_missing():
    root = MagicMock(spec=["children", "labels"])
    root.children = {}
    root.labels = {}

    with serve(PySerial(url="loop://", power_control_ref="nonexistent")) as client:
        object.__setattr__(client, "root", root)
        assert client._find_power_client() is None


def test_find_power_client_non_power_ignored():
    gpio = MagicMock(spec=["children", "labels"])
    gpio.children = {}
    gpio.labels = {"jumpstarter.dev/client": "jumpstarter_driver_gpiod.client.DigitalOutputClient"}
    root = MagicMock(spec=["children", "labels"])
    root.children = {"gpio": gpio}
    root.labels = {}

    with serve(PySerial(url="loop://")) as client:
        object.__setattr__(client, "root", root)
        assert client._find_power_client() is None


def test_make_power_cycle_custom_method():
    called_sequence = []
    power = MagicMock()
    power.off = MagicMock(side_effect=lambda: called_sequence.append("off"))
    power.on = MagicMock(side_effect=lambda: called_sequence.append("on"))

    with serve(PySerial(url="loop://", power_control_method=["off", "on"])) as client:
        cycle_fn = client._make_power_cycle(power)
        client.portal.call(cycle_fn)
        assert called_sequence == ["off", "on"]


def test_find_power_client_disabled_via_empty_list():
    power = MagicMock(spec=["children", "labels"])
    power.children = {}
    power.labels = {"jumpstarter.dev/client": "jumpstarter_driver_power.client.PowerClient"}
    root = MagicMock(spec=["children", "labels"])
    root.children = {"power": power}
    root.labels = {}

    with serve(PySerial(url="loop://", power_control_method=[])) as client:
        object.__setattr__(client, "root", root)
        assert client._find_power_client() is None


def test_make_power_cycle_missing_method():
    power = MagicMock(spec=["children", "labels"])
    power.children = {}
    power.labels = {}

    with serve(PySerial(url="loop://", power_control_method=["nonexistent_method"])) as client:
        result = client._make_power_cycle(power)
        assert result is None


def test_make_power_cycle_with_sleep():
    called_sequence = []
    power = MagicMock()
    power.off = MagicMock(side_effect=lambda: called_sequence.append("off"))
    power.on = MagicMock(side_effect=lambda: called_sequence.append("on"))

    with serve(PySerial(url="loop://", power_control_method=["off", "sleep:0.01", "on"])) as client:
        cycle_fn = client._make_power_cycle(power)
        client.portal.call(cycle_fn)
        assert called_sequence == ["off", "on"]


def test_find_power_client_disabled_via_none():
    power = MagicMock(spec=["children", "labels"])
    power.children = {}
    power.labels = {"jumpstarter.dev/client": "jumpstarter_driver_power.client.PowerClient"}
    root = MagicMock(spec=["children", "labels"])
    root.children = {"power": power}
    root.labels = {}

    with serve(PySerial(url="loop://", power_control_method=None)) as client:
        object.__setattr__(client, "root", root)
        assert client._find_power_client() is None
