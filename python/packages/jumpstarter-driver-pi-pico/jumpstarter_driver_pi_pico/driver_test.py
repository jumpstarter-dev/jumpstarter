import asyncio
from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest
from jumpstarter_driver_pyserial.driver import PySerial

from .driver import PiPico
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver


@dataclass(kw_only=True)
class _MockGPIO(Driver):
    """Minimal stand-in for a DigitalOutput child (records on/off calls)."""

    calls: list = field(default_factory=list)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"

    def on(self):
        self.calls.append("on")

    def off(self):
        self.calls.append("off")


def test_drivers_pi_pico_bootloader_info(monkeypatch, tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "INFO_UF2.TXT").write_text("Model: RP2040\nBoard-ID: RPI-RP2\n")
    monkeypatch.setattr(
        "jumpstarter_driver_pi_pico.driver.find_all_bootloader_mounts",
        lambda: [boot],
    )

    with serve(PiPico()) as client:
        info = client.bootloader_info()
        assert info["Model"] == "RP2040"
        assert info["Board-ID"] == "RPI-RP2"


def test_drivers_pi_pico_flash(monkeypatch, tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "INFO_UF2.TXT").write_text("x: y\n")
    firmware = tmp_path / "fw.uf2"
    firmware.write_bytes(b"UF2" * 400)
    monkeypatch.setattr(
        "jumpstarter_driver_pi_pico.driver.find_all_bootloader_mounts",
        lambda: [boot],
    )

    with serve(PiPico()) as client:
        client.flash(firmware, target="out.uf2")

    assert (boot / "out.uf2").read_bytes() == firmware.read_bytes()


def test_drivers_pi_pico_flash_default_name(monkeypatch, tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "INDEX.HTM").write_text("<html></html>")
    firmware = tmp_path / "fw.uf2"
    firmware.write_bytes(b"\xaa" * 256)
    monkeypatch.setattr(
        "jumpstarter_driver_pi_pico.driver.find_all_bootloader_mounts",
        lambda: [boot],
    )

    with serve(PiPico()) as client:
        client.flash(firmware)

    assert (boot / "Firmware.uf2").read_bytes() == firmware.read_bytes()


def test_drivers_pi_pico_multiple_boot_volumes_raises(monkeypatch, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "INFO_UF2.TXT").write_text("x: y\n")
    (b / "INFO_UF2.TXT").write_text("x: y\n")
    driver = PiPico()

    monkeypatch.setattr(
        "jumpstarter_driver_pi_pico.driver.find_all_bootloader_mounts",
        lambda: [a, b],
    )
    with pytest.raises(FileNotFoundError, match="Multiple Pico BOOTSEL"):
        driver._resolve_mount_path()


def test_drivers_pi_pico_auto_discover_single(monkeypatch, tmp_path):
    vol = tmp_path / "vol"
    vol.mkdir()
    (vol / "INFO_UF2.TXT").write_text("Model: auto\n")
    monkeypatch.setattr(
        "jumpstarter_driver_pi_pico.driver.find_all_bootloader_mounts",
        lambda: [vol],
    )
    with serve(PiPico()) as client:
        assert client.bootloader_info()["Model"] == "auto"


def test_drivers_pi_pico_enter_bootloader_via_serial(monkeypatch, tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "INFO_UF2.TXT").write_text("Model: boot\n")
    serial = PySerial(url="loop://", check_present=False)
    driver = PiPico(children={"serial": serial})
    mounts: list = []

    class _MockSerial:
        dtr = True

        def close(self):
            mounts[:] = [boot]

    monkeypatch.setattr(
        "jumpstarter_driver_pi_pico.driver.find_all_bootloader_mounts",
        lambda: mounts.copy(),
    )
    monkeypatch.setattr("jumpstarter_driver_pi_pico.driver.serial_for_url", lambda *args, **kwargs: _MockSerial())

    driver.enter_bootloader()
    assert driver.bootloader_info()["Model"] == "boot"


def test_drivers_pi_pico_flash_auto_enters_bootloader(monkeypatch, tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "INFO_UF2.TXT").write_text("x: y\n")
    firmware = tmp_path / "fw.uf2"
    firmware.write_bytes(b"\xbb" * 256)
    serial = PySerial(url="loop://", check_present=False)
    mounts: list = []

    class _MockSerial:
        dtr = True

        def close(self):
            mounts[:] = [boot]

    monkeypatch.setattr(
        "jumpstarter_driver_pi_pico.driver.find_all_bootloader_mounts",
        lambda: mounts.copy(),
    )
    monkeypatch.setattr("jumpstarter_driver_pi_pico.driver.serial_for_url", lambda *args, **kwargs: _MockSerial())

    with serve(PiPico(children={"serial": serial})) as client:
        client.flash(firmware)

    assert (boot / "Firmware.uf2").read_bytes() == firmware.read_bytes()


def test_drivers_pi_pico_dump_not_implemented(monkeypatch, tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "INFO_UF2.TXT").write_text("x: y\n")
    driver = PiPico()
    monkeypatch.setattr(
        "jumpstarter_driver_pi_pico.driver.find_all_bootloader_mounts",
        lambda: [boot],
    )

    with pytest.raises(NotImplementedError, match="not supported"):
        asyncio.run(driver.dump(None, None))


def test_drivers_pi_pico_enter_bootloader_via_gpio(monkeypatch, tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "INFO_UF2.TXT").write_text("Model: gpio\n")

    bootsel_gpio = _MockGPIO()
    run_gpio = _MockGPIO()
    driver = PiPico(children={"bootsel": bootsel_gpio, "run": run_gpio})

    call_count = 0

    def _fake_mounts():
        nonlocal call_count
        call_count += 1
        if call_count <= 1:
            return []
        return [boot]

    monkeypatch.setattr("jumpstarter_driver_pi_pico.driver.find_all_bootloader_mounts", _fake_mounts)

    driver.enter_bootloader()

    assert bootsel_gpio.calls == ["on", "off"]
    assert run_gpio.calls == ["on", "off"]


def test_drivers_pi_pico_gpio_preferred_over_serial(monkeypatch, tmp_path):
    """When both GPIO and serial children are present, GPIO is used."""
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "INFO_UF2.TXT").write_text("Model: gpio\n")

    bootsel_gpio = _MockGPIO()
    run_gpio = _MockGPIO()
    serial = PySerial(url="loop://", check_present=False)
    serial_touched = MagicMock()

    driver = PiPico(children={"bootsel": bootsel_gpio, "run": run_gpio, "serial": serial})

    call_count = 0

    def _fake_mounts():
        nonlocal call_count
        call_count += 1
        if call_count <= 1:
            return []
        return [boot]

    monkeypatch.setattr("jumpstarter_driver_pi_pico.driver.find_all_bootloader_mounts", _fake_mounts)
    monkeypatch.setattr(driver, "_touch_serial_for_bootloader", serial_touched)

    driver.enter_bootloader()

    assert bootsel_gpio.calls == ["on", "off"]
    assert run_gpio.calls == ["on", "off"]
    serial_touched.assert_not_called()


def test_drivers_pi_pico_flash_via_gpio(monkeypatch, tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "INFO_UF2.TXT").write_text("x: y\n")
    firmware = tmp_path / "fw.uf2"
    firmware.write_bytes(b"\xcc" * 256)

    bootsel_gpio = _MockGPIO()
    run_gpio = _MockGPIO()

    call_count = 0

    def _fake_mounts():
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return []
        return [boot]

    monkeypatch.setattr("jumpstarter_driver_pi_pico.driver.find_all_bootloader_mounts", _fake_mounts)

    with serve(PiPico(children={"bootsel": bootsel_gpio, "run": run_gpio})) as client:
        client.flash(firmware)

    assert (boot / "Firmware.uf2").read_bytes() == firmware.read_bytes()
    assert bootsel_gpio.calls == ["on", "off"]


def test_drivers_pi_pico_no_children_raises(monkeypatch):
    monkeypatch.setattr("jumpstarter_driver_pi_pico.driver.find_all_bootloader_mounts", lambda: [])
    driver = PiPico()
    with pytest.raises(NotImplementedError, match="GPIO children.*serial"):
        driver.enter_bootloader()
