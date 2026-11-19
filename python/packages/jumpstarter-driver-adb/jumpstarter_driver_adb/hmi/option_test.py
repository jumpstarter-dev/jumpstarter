"""The ``hmi:`` option of an ``AdbDevice``: the child it builds, what it refuses, when its package is imported and
that it is closed before the shared ADB server is released."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from unittest.mock import MagicMock, patch

import pytest
from jumpstarter_driver_hmi.driver import MockHmi

from ..driver import AdbDevice
from .client import AdbDeviceHmiClient
from .driver import AdbDeviceHmi
from .fakes import invoke
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve

SERIAL = "R58M123ABC"


@pytest.fixture(autouse=True)
def adb(monkeypatch):
    """No adb on the test host: the device is present, and the shared server is a stand-in."""
    monkeypatch.setattr("jumpstarter_driver_adb.driver._resolve_adb_path", lambda path, timeout: "/usr/bin/adb")
    monkeypatch.setattr(AdbDevice, "adb_env", MagicMock(return_value={}))
    present = {"transport": "usb", "present": "yes", "serial": SERIAL}
    monkeypatch.setattr(AdbDevice, "info", MagicMock(return_value=present))
    monkeypatch.setattr(AdbDevice, "_visible_devices", MagicMock(return_value=[(SERIAL, "device", None)]))


# -- the child -----------------------------------------------------------------------------------------------------


def test_the_hmi_child_exists_only_when_configured():
    assert "hmi" not in AdbDevice(serial=SERIAL).children
    device = AdbDevice(serial=SERIAL, hmi={})
    child = device.children["hmi"]
    assert isinstance(child, AdbDeviceHmi) and child.device is device
    assert child.control is True and child.encoder_policy == "hardware"


def test_the_options_of_the_option_are_the_hmis():
    device = AdbDevice(
        serial=SERIAL,
        hmi={"control": False, "max_size": 960, "display_id": 1, "hard_keys": ["HOME"], "idle_stop_s": 5},
    )
    child = device.children["hmi"]
    assert isinstance(child, AdbDeviceHmi)
    assert (child.control, child.max_size, child.display_id, child.hard_keys, child.idle_stop_s) == (
        False,
        960,
        1,
        ["HOME"],
        5,
    )


def test_a_hmi_and_an_appium_are_both_children():
    device = AdbDevice(serial=SERIAL, appium={}, hmi={})
    assert sorted(device.children) == ["appium", "hmi"]


def test_a_hand_written_child_of_that_name_is_refused():
    with pytest.raises(ConfigurationError, match="child hmi conflicts with the hmi option"):
        AdbDevice(serial=SERIAL, hmi={}, children={"hmi": MockHmi()})
    assert "hmi" in AdbDevice(serial=SERIAL, children={"hmi": MockHmi()}).children  # without the option: fine


@pytest.mark.parametrize("config", [{"nope": 1}, {"device": "x"}, {"children": {}}, {"hard_key": ["HOME"]}])
def test_an_option_the_hmi_does_not_have_is_a_configuration_error_and_not_a_type_error(config):
    with pytest.raises(ConfigurationError, match="hmi accepts only"):
        AdbDevice(serial=SERIAL, hmi=config)


@pytest.mark.parametrize("config", ["hmi", ["control"], 5, True])
def test_the_option_must_be_a_mapping(config):
    with pytest.raises(ConfigurationError, match="must be a mapping"):
        AdbDevice(serial=SERIAL, hmi=config)


@pytest.mark.parametrize(
    "config",
    [{"max_size": -1}, {"control": "yes"}, {"server_sha256": "abc"}, {"encoder_policy": "x"}, {"stall_s": "x"}],
)
def test_a_wrong_value_is_refused_when_the_device_is_configured(config):
    with pytest.raises(ConfigurationError):
        AdbDevice(serial=SERIAL, hmi=config)


def test_the_child_is_built_without_touching_the_device_or_a_server():
    device = AdbDevice(serial=SERIAL, hmi={})
    child = device.children["hmi"]
    assert isinstance(child, AdbDeviceHmi)
    assert device._server is None and child._source is None
    AdbDevice.info.assert_not_called()
    AdbDevice.adb_env.assert_not_called()  # ty: ignore[unresolved-attribute]


# -- when the HMI package is imported ---------------------------------------------------------------------------


def run_python(code: str) -> subprocess.CompletedProcess:
    """``code`` in a Python of its own (what has been imported is the question), with no adb on the host."""
    prelude = """
    import sys
    from unittest.mock import patch

    patch("jumpstarter_driver_adb.driver._resolve_adb_path", lambda path, timeout: "/usr/bin/adb").start()
    from jumpstarter_driver_adb.driver import AdbDevice

    def hmi_modules():
        return sorted(
            m for m in sys.modules if m.startswith(("jumpstarter_driver_hmi", "jumpstarter_driver_adb.hmi"))
        )
    """
    program = textwrap.dedent(prelude) + textwrap.dedent(code)
    return subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=120, check=False)


def test_a_device_without_the_option_never_imports_the_hmi():
    done = run_python(
        """
        device = AdbDevice(serial="X")
        assert "hmi" not in device.children
        assert not hmi_modules(), hmi_modules()
        """
    )
    assert done.returncode == 0, done.stderr


def test_a_device_with_the_option_imports_it():
    done = run_python(
        """
        device = AdbDevice(serial="X", hmi={})
        assert "jumpstarter_driver_hmi.driver" in sys.modules
        assert "jumpstarter_driver_adb.hmi.driver" in sys.modules
        """
    )
    assert done.returncode == 0, done.stderr


def test_the_helpers_that_need_no_hmi_package_can_be_used_without_it():
    done = run_python(
        """
        from jumpstarter_driver_adb.hmi import adb, fetch, protocol, server_binary
        assert not [m for m in hmi_modules() if m.startswith("jumpstarter_driver_hmi")], hmi_modules()
        """
    )
    assert done.returncode == 0, done.stderr


@pytest.fixture
def without_the_hmi_package(monkeypatch):
    """The ``hmi`` extra is not installed: importing ``jumpstarter_driver_hmi`` fails, and what imported it before
    is imported again."""
    for name in list(sys.modules):
        if name == "jumpstarter_driver_hmi" or name.startswith(
            ("jumpstarter_driver_hmi.", "jumpstarter_driver_adb.hmi.")
        ):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "jumpstarter_driver_hmi", None)


def test_without_the_extra_the_configuration_is_refused_with_what_to_install(without_the_hmi_package):
    with pytest.raises(ConfigurationError, match=r"pip install jumpstarter-driver-adb\[hmi\]"):
        AdbDevice(serial=SERIAL, hmi={})
    assert "hmi" not in AdbDevice(serial=SERIAL).children  # a device that does not ask for one does not need it


def test_an_import_error_that_is_not_the_missing_extra_is_not_reported_as_one(monkeypatch):
    for name in list(sys.modules):
        if name.startswith("jumpstarter_driver_adb.hmi."):
            monkeypatch.delitem(sys.modules, name)  # imported again, when the device asks for its HMI
    monkeypatch.setitem(sys.modules, "jumpstarter_driver_adb.hmi.protocol", None)  # and something is broken in it
    with pytest.raises(ImportError) as raised:
        AdbDevice(serial=SERIAL, hmi={})
    assert not isinstance(raised.value, ConfigurationError) and "protocol" in str(raised.value)


# -- closing -------------------------------------------------------------------------------------------------------


def test_the_hmi_is_closed_before_the_adb_server_is_released():
    device = AdbDevice(serial=SERIAL, hmi={})
    child = device.children["hmi"]
    order: list[str] = []
    with (
        patch.object(child, "close", side_effect=lambda: order.append("hmi")),
        patch("jumpstarter_driver_adb.driver._release_server", side_effect=lambda *a, **k: order.append("server")),
    ):
        device._server = MagicMock()
        device.close()
    assert order[:2] == ["hmi", "server"]


def test_the_hmi_and_appium_are_both_closed_before_the_adb_server_is_released():
    device = AdbDevice(serial=SERIAL, appium={}, hmi={})
    order: list[str] = []
    with (
        patch.object(device.children["hmi"], "close", side_effect=lambda: order.append("hmi")),
        patch.object(device.children["appium"], "close", side_effect=lambda: order.append("appium")),
        patch("jumpstarter_driver_adb.driver._release_server", side_effect=lambda *a, **k: order.append("server")),
    ):
        device._server = MagicMock()
        device.close()
    assert order[:3] == ["hmi", "appium", "server"]


def test_closing_the_device_stops_a_running_stream(tmp_path):
    from .fakes import FakeAdb

    jar = tmp_path / "scrcpy-server"
    jar.write_bytes(b"PK\x03\x04 a server")
    import hashlib

    device = AdbDevice(
        serial=SERIAL,
        hmi={"server_path": str(jar), "server_sha256": hashlib.sha256(jar.read_bytes()).hexdigest()},
    )
    child = device.children["hmi"]
    assert isinstance(child, AdbDeviceHmi)
    fake = FakeAdb()
    child._adb = fake  # ty: ignore[invalid-assignment]
    try:
        child.start()
        assert child.status()["state"] == "running"
        with patch("jumpstarter_driver_adb.driver._release_server"):
            device._server = MagicMock()
            device.close()
        assert child.status()["state"] == "stopped" and fake.process is not None and fake.process.returncode is not None
    finally:
        fake.close()


# -- the device and its HMI on the client's side ----------------------------------------------------------------


def test_the_serial_the_hmi_uses_is_the_one_the_device_resolves_now():
    device = AdbDevice(serial=SERIAL, hmi={})
    child = device.children["hmi"]
    assert isinstance(child, AdbDeviceHmi)
    assert child.device.current_serial() == SERIAL
    AdbDevice._visible_devices.return_value = [(SERIAL, "offline", None)]  # ty: ignore[unresolved-attribute]
    with pytest.raises(RuntimeError, match="is offline"):
        child.device.current_serial()


def test_the_device_client_has_the_hmi_and_its_commands_when_the_exporter_configures_one():
    with serve(AdbDevice(serial=SERIAL, hmi={})) as client:
        assert isinstance(client.hmi, AdbDeviceHmiClient)
        group = client.cli()
        assert "hmi" in group.commands
        result = invoke(group, ["hmi", "info", "--json"])
        assert result.exit_code == 0 and '"max_contacts": 1' in result.output and '"hevc"' in result.output


def test_the_device_client_without_a_hmi_says_so():
    with serve(AdbDevice(serial=SERIAL)) as client:
        with pytest.raises(AttributeError, match="HMI is not configured"):
            _ = client.hmi
        assert "hmi" not in client.cli().commands
