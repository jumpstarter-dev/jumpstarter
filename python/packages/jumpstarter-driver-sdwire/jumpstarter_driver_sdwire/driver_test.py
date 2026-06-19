import json
import logging
import subprocess
from typing import cast

import anyio
import pytest
import usb
import usb.core

from jumpstarter_driver_sdwire import driver as sdwire_driver
from jumpstarter_driver_sdwire.driver import SDWire

from jumpstarter.common.utils import serve


def _sdwire_hub(ft200x_serial, disk):
    """One SD Wire onboard hub (SMSC 0x2640) holding an FT200X controller and the
    SMSC SD reader as direct siblings. Mirrors real `system_profiler` output, where
    the disk identifier is nested under the reader's `Media` list (not top-level)."""
    return {
        "_name": "hub_device",
        "vendor_id": "0x0424  (SMSC)",
        "product_id": "0x2640",
        "_items": [
            {
                "_name": "FT200X USB I2C",
                "vendor_id": "0x0403  (Future Technology Devices International Limited)",
                "product_id": "0x6015",
                "serial_num": ft200x_serial,
            },
            {
                "_name": "Ultra Fast Media Reader",
                "vendor_id": "0x0424  (SMSC)",
                "product_id": "0x4050",
                "serial_num": "000000264001",
                "Media": [
                    {
                        "_name": "Ultra HS-SD/MMC",
                        "bsd_name": disk,
                        "volumes": [{"_name": "ESP", "bsd_name": f"{disk}s1"}],
                    }
                ],
            },
        ],
    }


def _sp_tree(*hubs):
    return {"SPUSBDataType": [{"_name": "USB31Bus", "_items": list(hubs)}]}


def _patch_system_profiler(monkeypatch, tree):
    monkeypatch.setattr(sdwire_driver.subprocess, "check_output", lambda *a, **k: json.dumps(tree))


def test_macos_storage_correlates_by_serial(monkeypatch):
    _patch_system_profiler(monkeypatch, _sp_tree(_sdwire_hub("DP04I34D", "disk6"), _sdwire_hub("OTHER999", "disk8")))
    assert sdwire_driver._find_storage_device_macos("DP04I34D") == "/dev/disk6"
    assert sdwire_driver._find_storage_device_macos("OTHER999") == "/dev/disk8"


def test_macos_storage_ambiguous_without_serial(monkeypatch):
    _patch_system_profiler(monkeypatch, _sp_tree(_sdwire_hub("DP04I34D", "disk6"), _sdwire_hub("OTHER999", "disk8")))
    with pytest.raises(RuntimeError):
        sdwire_driver._find_storage_device_macos(None)


def test_macos_storage_single_device(monkeypatch):
    _patch_system_profiler(monkeypatch, _sp_tree(_sdwire_hub("DP04I34D", "disk6")))
    # unambiguous even without a serial
    assert sdwire_driver._find_storage_device_macos(None) == "/dev/disk6"


def test_macos_storage_wrong_serial(monkeypatch):
    _patch_system_profiler(monkeypatch, _sp_tree(_sdwire_hub("DP04I34D", "disk6")))
    assert sdwire_driver._find_storage_device_macos("NOPE") is None


def test_macos_storage_not_found(monkeypatch):
    _patch_system_profiler(monkeypatch, {"SPUSBDataType": []})
    assert sdwire_driver._find_storage_device_macos("DP04I34D") is None


class _FakeUSBDev:
    def __init__(self, serial):
        self._serial = serial
        self.iSerialNumber = 1
        self.iProduct = 2
        self.bus = 1
        self.address = 2
        self.port_numbers = ()


def test_ft200x_requires_explicit_serial(monkeypatch):
    ft200x = _FakeUSBDev("DP04I34D")

    def fake_find(idVendor, idProduct, find_all):
        # only the FT200X VID/PID enumerates a device; no programmed sd-wire present
        if (idVendor, idProduct) == (sdwire_driver._FTDI_VID, sdwire_driver._FT200X_PID):
            return iter([ft200x])
        return iter([])

    monkeypatch.setattr(sdwire_driver.usb.core, "find", fake_find)
    monkeypatch.setattr(sdwire_driver.usb.util, "get_string", lambda dev, idx: dev._serial)

    # without a configured serial, an FT200X must NOT be selected (no reliable identity)
    sdwire = object.__new__(SDWire)
    sdwire.serial = None
    sdwire.logger = logging.getLogger("test-sdwire")
    assert sdwire._find_device() is None

    # with the matching serial configured, it is selected
    sdwire.serial = "DP04I34D"
    found = sdwire._find_device()
    assert found is not None
    assert found[0] is ft200x
    assert found[1] == "DP04I34D"


def test_find_device_ambiguous_programmed_without_serial(monkeypatch):
    dev_a = _FakeUSBDev("AAAA")
    dev_b = _FakeUSBDev("BBBB")
    for d in (dev_a, dev_b):
        d.iProduct = 2

    def fake_find(idVendor, idProduct, find_all):
        # two programmed "sd-wire" units enumerate; no FT200X
        if (idVendor, idProduct) == (sdwire_driver._PROGRAMMED_VID, sdwire_driver._PROGRAMMED_PID):
            return iter([dev_a, dev_b])
        return iter([])

    def fake_get_string(dev, idx):
        return sdwire_driver._PROGRAMMED_PRODUCT if idx == dev.iProduct else dev._serial

    monkeypatch.setattr(sdwire_driver.usb.core, "find", fake_find)
    monkeypatch.setattr(sdwire_driver.usb.util, "get_string", fake_get_string)

    sdwire = object.__new__(SDWire)
    sdwire.serial = None
    sdwire.logger = logging.getLogger("test-sdwire")

    # two units, no serial -> must fail loudly instead of binding the first one
    with pytest.raises(RuntimeError, match="multiple sd-wire devices"):
        sdwire._find_device()

    # selecting one by serial resolves the ambiguity
    sdwire.serial = "BBBB"
    found = sdwire._find_device()
    assert found is not None
    assert found[0] is dev_b


def test_poll_storage_device_retries(monkeypatch):
    sdwire = object.__new__(SDWire)
    calls = iter([None, None, "/dev/disk6"])
    monkeypatch.setattr(SDWire, "effective_storage_device", lambda self: next(calls))
    monkeypatch.setattr(sdwire_driver.time, "sleep", lambda _s: None)
    assert sdwire._poll_storage_device(10) == "/dev/disk6"


def test_poll_storage_device_times_out(monkeypatch):
    sdwire = object.__new__(SDWire)
    monkeypatch.setattr(SDWire, "effective_storage_device", lambda self: None)
    monkeypatch.setattr(sdwire_driver.time, "sleep", lambda _s: None)
    assert sdwire._poll_storage_device(0) is None


def test_await_storage_device_retries(monkeypatch):
    sdwire = object.__new__(SDWire)
    calls = iter([None, None, "/dev/disk6"])
    monkeypatch.setattr(SDWire, "effective_storage_device", lambda self: next(calls))

    async def no_sleep(_s):
        pass

    monkeypatch.setattr(sdwire_driver.anyio, "sleep", no_sleep)
    assert anyio.run(sdwire._await_storage_device, 10) == "/dev/disk6"


def test_dut_aborts_when_eject_fails(monkeypatch):
    sdwire = object.__new__(SDWire)
    sdwire.storage_device = "/dev/disk4"
    sdwire.storage_timeout = 10
    sdwire.logger = logging.getLogger("test-sdwire")

    monkeypatch.setattr(sdwire_driver.platform, "system", lambda: "Darwin")

    switched = []
    monkeypatch.setattr(SDWire, "select", lambda self, target: switched.append(target))

    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="disk busy")

    monkeypatch.setattr(sdwire_driver.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="busy"):
        sdwire.dut()
    # the card must NOT be switched to the DUT when the eject failed
    assert switched == []


def test_dut_switches_after_successful_eject(monkeypatch):
    sdwire = object.__new__(SDWire)
    sdwire.storage_device = "/dev/disk4"
    sdwire.storage_timeout = 10
    sdwire.logger = logging.getLogger("test-sdwire")

    monkeypatch.setattr(sdwire_driver.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sdwire_driver.time, "sleep", lambda _s: None)

    switched = []
    monkeypatch.setattr(SDWire, "select", lambda self, target: switched.append(target))
    monkeypatch.setattr(sdwire_driver.subprocess, "run", lambda *a, **k: None)

    sdwire.dut()
    assert switched == [0xF0]


def test_dut_aborts_when_disk_unknown(monkeypatch):
    sdwire = object.__new__(SDWire)
    sdwire.storage_timeout = 0  # do not actually wait
    sdwire.logger = logging.getLogger("test-sdwire")

    monkeypatch.setattr(sdwire_driver.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(SDWire, "effective_storage_device", lambda self: None)
    monkeypatch.setattr(sdwire_driver.time, "sleep", lambda _s: None)

    switched = []
    monkeypatch.setattr(SDWire, "select", lambda self, target: switched.append(target))
    ejected = []
    monkeypatch.setattr(sdwire_driver.subprocess, "run", lambda *a, **k: ejected.append(a))

    with pytest.raises(RuntimeError, match="refusing to switch"):
        sdwire.dut()

    # discovery failed: no eject, no switch
    assert ejected == []
    assert switched == []


def test_write_raises_when_disk_unavailable(monkeypatch):
    sdwire = object.__new__(SDWire)
    sdwire.storage_timeout = 0
    sdwire.logger = logging.getLogger("test-sdwire")

    monkeypatch.setattr(SDWire, "host", lambda self: None)

    async def none_await(self, timeout):
        return None

    monkeypatch.setattr(SDWire, "_await_storage_device", none_await)

    # a clear RuntimeError, not a TypeError from os.open(None, ...)
    with pytest.raises(RuntimeError, match="did not become available"):
        anyio.run(sdwire.write, "dummy")


class _FakeHub:
    def __init__(self, bus, port_numbers):
        self.bus = bus
        self.port_numbers = port_numbers
        self.calls = []

    def ctrl_transfer(self, *args):
        self.calls.append(args)


def test_host_selects_before_power_cycle(monkeypatch):
    sdwire = object.__new__(SDWire)
    sdwire.logger = logging.getLogger("test-sdwire")
    sdwire.dev = object()

    monkeypatch.setattr(sdwire_driver.platform, "system", lambda: "Darwin")

    events = []
    monkeypatch.setattr(SDWire, "select", lambda self, target: events.append(("select", target)))
    monkeypatch.setattr(
        sdwire_driver,
        "_power_cycle_smsc_port",
        lambda dev, logger=None: events.append(("power_cycle", dev)),
    )

    sdwire.host()

    # the card must be routed back to the SMSC slot BEFORE the reader is power-cycled
    assert events == [("select", 0xF1), ("power_cycle", sdwire.dev)]


def test_host_no_power_cycle_off_darwin(monkeypatch):
    sdwire = object.__new__(SDWire)
    sdwire.logger = logging.getLogger("test-sdwire")
    sdwire.dev = object()

    monkeypatch.setattr(sdwire_driver.platform, "system", lambda: "Linux")

    events = []
    monkeypatch.setattr(SDWire, "select", lambda self, target: events.append(("select", target)))
    monkeypatch.setattr(
        sdwire_driver,
        "_power_cycle_smsc_port",
        lambda dev, logger=None: events.append(("power_cycle", dev)),
    )

    sdwire.host()
    assert events == [("select", 0xF1)]


def test_power_cycle_scopes_to_parent_hub(monkeypatch):
    # FT200X at bus 2, port path (5, 2) -> its parent hub path is (5,)
    dev = _FakeUSBDev("DP04I34D")
    dev.bus = 2
    dev.port_numbers = (5, 2)

    right_hub = _FakeHub(bus=2, port_numbers=(5,))
    wrong_bus = _FakeHub(bus=3, port_numbers=(5,))
    wrong_path = _FakeHub(bus=2, port_numbers=(9,))

    monkeypatch.setattr(
        sdwire_driver.usb.core,
        "find",
        lambda **k: iter([wrong_bus, wrong_path, right_hub]),
    )
    monkeypatch.setattr(sdwire_driver.time, "sleep", lambda _s: None)

    sdwire_driver._power_cycle_smsc_port(cast(usb.core.Device, dev))

    # only the correlated hub is power-cycled; the others are left alone
    assert right_hub.calls == [
        (0x23, 1, 8, sdwire_driver._SMSC_HUB_PORT, None),
        (0x23, 3, 8, sdwire_driver._SMSC_HUB_PORT, None),
    ]
    assert wrong_bus.calls == []
    assert wrong_path.calls == []


def test_power_cycle_skips_when_no_topology_match(monkeypatch):
    dev = _FakeUSBDev("DP04I34D")
    dev.bus = 2
    dev.port_numbers = (5, 2)

    # two hubs present, neither matches the dev's topology -> do not guess
    hub_a = _FakeHub(bus=2, port_numbers=(7,))
    hub_b = _FakeHub(bus=2, port_numbers=(8,))
    monkeypatch.setattr(sdwire_driver.usb.core, "find", lambda **k: iter([hub_a, hub_b]))
    monkeypatch.setattr(sdwire_driver.time, "sleep", lambda _s: None)

    sdwire_driver._power_cycle_smsc_port(cast(usb.core.Device, dev))
    assert hub_a.calls == []
    assert hub_b.calls == []


def test_drivers_sdwire():
    try:
        instance = SDWire()
    except FileNotFoundError:
        pytest.skip("sd-wire not available")  # ty: ignore[call-non-callable]
    except usb.core.USBError:
        pytest.skip("USB not available")  # ty: ignore[call-non-callable]
    except usb.core.NoBackendError:
        pytest.skip("No USB backend")  # ty: ignore[call-non-callable]

    with serve(instance) as client:
        client.host()
        assert instance.query() == "host"
        client.dut()
        assert instance.query() == "dut"
