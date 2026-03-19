from unittest.mock import MagicMock, PropertyMock

import pytest
from jumpstarter_driver_pyserial.driver import PySerial

from .driver import Esp32Flasher, _parse_region
from jumpstarter.common.utils import serve


class _MockEsp:
    CHIP_NAME = "ESP32-D0WD-V3"
    IS_STUB = False

    def get_chip_description(self):
        return "ESP32-D0WD-V3 (revision v3.1)"

    def get_chip_features(self):
        return ["Wi-Fi", "BT", "Dual Core"]

    def read_mac(self):
        return [0x5C, 0x01, 0x3B, 0x68, 0xAB, 0x0C]

    def run_stub(self):
        self.IS_STUB = True
        return self

    def hard_reset(self):
        pass


def _make_driver():
    serial = PySerial(url="loop://", check_present=False)
    return Esp32Flasher(children={"serial": serial})


@pytest.fixture()
def mock_esptool(monkeypatch):
    mock_esp = _MockEsp()
    mocks: dict = {
        "detect_chip": MagicMock(return_value=mock_esp),
        "write_flash": MagicMock(),
        "read_flash": MagicMock(),
        "erase_flash": MagicMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(f"esptool.cmds.{name}", mock)
    mocks["esp"] = mock_esp
    return mocks


def test_get_chip_info(mock_esptool):
    with serve(_make_driver()) as client:
        info = client.get_chip_info()
        assert "ESP32" in info["chip"]
        assert "Wi-Fi" in info["features"]
        assert info["mac"] == "5c:01:3b:68:ab:0c"


def test_flash(mock_esptool, tmp_path):
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"\xde\xad" * 512)

    with serve(_make_driver()) as client:
        client.flash(firmware, target="0x10000")

    mock_esptool["write_flash"].assert_called_once()
    _, flash_entries = mock_esptool["write_flash"].call_args[0]
    assert flash_entries[0][0] == 0x10000


def test_flash_default_address(mock_esptool, tmp_path):
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"\x00" * 64)

    with serve(_make_driver()) as client:
        client.flash(firmware)

    _, flash_entries = mock_esptool["write_flash"].call_args[0]
    assert flash_entries[0][0] == 0x0


def test_dump(mock_esptool, tmp_path):
    test_data = b"flash content"

    def fake_read_flash(esp, address, size, filename):
        with open(filename, "wb") as f:
            f.write(test_data)

    mock_esptool["read_flash"].side_effect = fake_read_flash

    output = tmp_path / "dump.bin"
    with serve(_make_driver()) as client:
        client.dump(output, target="0x0:0x1000")

    assert output.read_bytes() == test_data


def test_erase(mock_esptool):
    with serve(_make_driver()) as client:
        client.erase()

    mock_esptool["erase_flash"].assert_called_once()


def _make_mock_serial():
    """Create a mock serial with tracked .dtr and .rts property assignments."""
    mock_serial = MagicMock()
    dtr_prop = PropertyMock()
    rts_prop = PropertyMock()
    type(mock_serial).dtr = dtr_prop
    type(mock_serial).rts = rts_prop
    return mock_serial, dtr_prop, rts_prop


def test_hard_reset(mock_esptool, monkeypatch):
    mock_serial, dtr_prop, rts_prop = _make_mock_serial()
    monkeypatch.setattr(
        "jumpstarter_driver_pyserial.driver.serial_for_url",
        MagicMock(return_value=mock_serial),
    )

    with serve(_make_driver()) as client:
        client.hard_reset()

    dtr_calls = [c.args[0] for c in dtr_prop.call_args_list]
    rts_calls = [c.args[0] for c in rts_prop.call_args_list]
    assert dtr_calls == [False]
    assert rts_calls == [True, False]


def test_enter_bootloader(mock_esptool, monkeypatch):
    mock_serial, dtr_prop, rts_prop = _make_mock_serial()
    monkeypatch.setattr(
        "jumpstarter_driver_pyserial.driver.serial_for_url",
        MagicMock(return_value=mock_serial),
    )

    with serve(_make_driver()) as client:
        client.enter_bootloader()

    dtr_calls = [c.args[0] for c in dtr_prop.call_args_list]
    rts_calls = [c.args[0] for c in rts_prop.call_args_list]
    assert dtr_calls == [False, True, False]
    assert rts_calls == [True, False]


def test_parse_region_default():
    assert _parse_region(None) == (0x0, 0x400000)


def test_parse_region_address_only():
    assert _parse_region("0x1000") == (0x1000, 0x400000)


def test_parse_region_address_and_size():
    assert _parse_region("0x1000:0x2000") == (0x1000, 0x2000)


def test_parse_region_decimal():
    assert _parse_region("4096:8192") == (4096, 8192)
