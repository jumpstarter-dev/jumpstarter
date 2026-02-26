from unittest.mock import MagicMock, call, patch

import pytest

from .driver import NoyitoPowerHID, NoyitoPowerSerial, _build_command
from jumpstarter.common.utils import serve

# ---------------------------------------------------------------------------
# Protocol unit tests (no mocking needed)
# ---------------------------------------------------------------------------


def test_build_command_ch1_on():
    assert _build_command(1, 1) == bytes([0xA0, 0x01, 0x01, 0xA2])


def test_build_command_ch1_off():
    assert _build_command(1, 0) == bytes([0xA0, 0x01, 0x00, 0xA1])


def test_build_command_ch2_on():
    assert _build_command(2, 1) == bytes([0xA0, 0x02, 0x01, 0xA3])


def test_build_command_ch2_off():
    assert _build_command(2, 0) == bytes([0xA0, 0x02, 0x00, 0xA2])


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


def test_channel_too_high():
    with pytest.raises(ValueError):
        NoyitoPowerSerial(port="/dev/ttyUSB0", channel=3)


def test_channel_too_low():
    with pytest.raises(ValueError):
        NoyitoPowerSerial(port="/dev/ttyUSB0", channel=0)


# ---------------------------------------------------------------------------
# Integration tests via serve() with serial.Serial mocked
# ---------------------------------------------------------------------------


def _make_serial_mock():
    mock_serial = MagicMock()
    mock_serial.__enter__ = MagicMock(return_value=mock_serial)
    mock_serial.__exit__ = MagicMock(return_value=False)
    return mock_serial


@patch("jumpstarter_driver_noyito_relay.driver.serial.Serial")
def test_on_ch1(mock_serial_cls):
    mock_ser = _make_serial_mock()
    mock_serial_cls.return_value = mock_ser

    with serve(NoyitoPowerSerial(port="/dev/ttyUSB0", channel=1)) as client:
        client.on()

    mock_ser.write.assert_called_once_with(bytes([0xA0, 0x01, 0x01, 0xA2]))


@patch("jumpstarter_driver_noyito_relay.driver.serial.Serial")
def test_off_ch1(mock_serial_cls):
    mock_ser = _make_serial_mock()
    mock_serial_cls.return_value = mock_ser

    with serve(NoyitoPowerSerial(port="/dev/ttyUSB0", channel=1)) as client:
        client.off()

    mock_ser.write.assert_called_once_with(bytes([0xA0, 0x01, 0x00, 0xA1]))


@patch("jumpstarter_driver_noyito_relay.driver.serial.Serial")
def test_on_ch2(mock_serial_cls):
    mock_ser = _make_serial_mock()
    mock_serial_cls.return_value = mock_ser

    with serve(NoyitoPowerSerial(port="/dev/ttyUSB0", channel=2)) as client:
        client.on()

    mock_ser.write.assert_called_once_with(bytes([0xA0, 0x02, 0x01, 0xA3]))


@patch("jumpstarter_driver_noyito_relay.driver.serial.Serial")
def test_off_ch2(mock_serial_cls):
    mock_ser = _make_serial_mock()
    mock_serial_cls.return_value = mock_ser

    with serve(NoyitoPowerSerial(port="/dev/ttyUSB0", channel=2)) as client:
        client.off()

    mock_ser.write.assert_called_once_with(bytes([0xA0, 0x02, 0x00, 0xA2]))


@patch("jumpstarter_driver_noyito_relay.driver.serial.Serial")
def test_read(mock_serial_cls):
    mock_ser = _make_serial_mock()
    mock_serial_cls.return_value = mock_ser

    with serve(NoyitoPowerSerial(port="/dev/ttyUSB0", channel=1)) as client:
        readings = list(client.read())

    assert len(readings) == 1
    assert readings[0].voltage == 0.0
    assert readings[0].current == 0.0


@patch("jumpstarter_driver_noyito_relay.driver.serial.Serial")
def test_status_ch1(mock_serial_cls):
    mock_ser = _make_serial_mock()
    mock_ser.read.return_value = b"CH1:ON \r\nCH2:OFF \r\n"
    mock_serial_cls.return_value = mock_ser

    with serve(NoyitoPowerSerial(port="/dev/ttyUSB0", channel=1)) as client:
        assert client.status() == "on"


@patch("jumpstarter_driver_noyito_relay.driver.serial.Serial")
def test_status_ch2(mock_serial_cls):
    mock_ser = _make_serial_mock()
    mock_ser.read.return_value = b"CH1:ON \r\nCH2:OFF \r\n"
    mock_serial_cls.return_value = mock_ser

    with serve(NoyitoPowerSerial(port="/dev/ttyUSB0", channel=2)) as client:
        assert client.status() == "off"


# ---------------------------------------------------------------------------
# Dual-channel (high-current) mode tests
# ---------------------------------------------------------------------------


@patch("jumpstarter_driver_noyito_relay.driver.serial.Serial")
def test_dual_on(mock_serial_cls):
    mock_ser = _make_serial_mock()
    mock_serial_cls.return_value = mock_ser

    with serve(NoyitoPowerSerial(port="/dev/ttyUSB0", dual=True)) as client:
        client.on()

    write_calls = mock_ser.write.call_args_list
    assert write_calls[0] == call(bytes([0xA0, 0x01, 0x01, 0xA2]))
    assert write_calls[1] == call(bytes([0xA0, 0x02, 0x01, 0xA3]))


@patch("jumpstarter_driver_noyito_relay.driver.serial.Serial")
def test_dual_off(mock_serial_cls):
    mock_ser = _make_serial_mock()
    mock_serial_cls.return_value = mock_ser

    with serve(NoyitoPowerSerial(port="/dev/ttyUSB0", dual=True)) as client:
        client.off()

    write_calls = mock_ser.write.call_args_list
    assert write_calls[0] == call(bytes([0xA0, 0x01, 0x00, 0xA1]))
    assert write_calls[1] == call(bytes([0xA0, 0x02, 0x00, 0xA2]))


@patch("jumpstarter_driver_noyito_relay.driver.serial.Serial")
def test_dual_status_both_on(mock_serial_cls):
    mock_ser = _make_serial_mock()
    mock_ser.read.return_value = b"CH1:ON \r\nCH2:ON \r\n"
    mock_serial_cls.return_value = mock_ser

    with serve(NoyitoPowerSerial(port="/dev/ttyUSB0", dual=True)) as client:
        assert client.status() == "on"


@patch("jumpstarter_driver_noyito_relay.driver.serial.Serial")
def test_dual_status_partial(mock_serial_cls):
    mock_ser = _make_serial_mock()
    mock_ser.read.return_value = b"CH1:ON \r\nCH2:OFF \r\n"
    mock_serial_cls.return_value = mock_ser

    with serve(NoyitoPowerSerial(port="/dev/ttyUSB0", dual=True)) as client:
        assert client.status() == "partial"


@patch("jumpstarter_driver_noyito_relay.driver.serial.Serial")
def test_cycle(mock_serial_cls):
    mock_ser = _make_serial_mock()
    mock_serial_cls.return_value = mock_ser

    with serve(NoyitoPowerSerial(port="/dev/ttyUSB0", channel=1)) as client:
        client.cycle(wait=0)

    write_calls = mock_ser.write.call_args_list
    assert write_calls[0] == call(bytes([0xA0, 0x01, 0x00, 0xA1]))
    assert write_calls[1] == call(bytes([0xA0, 0x01, 0x01, 0xA2]))


# ---------------------------------------------------------------------------
# NoyitoPowerHID validation tests
# ---------------------------------------------------------------------------


def test_hid_invalid_num_channels():
    with pytest.raises(ValueError):
        NoyitoPowerHID(num_channels=3)


def test_hid_channel_too_high_4ch():
    with pytest.raises(ValueError):
        NoyitoPowerHID(num_channels=4, channel=5)


def test_hid_channel_too_high_8ch():
    with pytest.raises(ValueError):
        NoyitoPowerHID(num_channels=8, channel=9)


# ---------------------------------------------------------------------------
# NoyitoPowerHID integration tests via serve() with hid.Device mocked
# ---------------------------------------------------------------------------


def _make_hid_mock():
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    return m


@patch("hid.Device")
def test_hid_on_ch3_4ch(mock_hid_cls):
    mock_dev = _make_hid_mock()
    mock_hid_cls.return_value = mock_dev

    with serve(NoyitoPowerHID(num_channels=4, channel=3)) as client:
        client.on()

    mock_dev.write.assert_called_once_with(b"\x00" + bytes([0xA0, 0x03, 0x01, 0xA4]))


@patch("hid.Device")
def test_hid_off_ch3_4ch(mock_hid_cls):
    mock_dev = _make_hid_mock()
    mock_hid_cls.return_value = mock_dev

    with serve(NoyitoPowerHID(num_channels=4, channel=3)) as client:
        client.off()

    mock_dev.write.assert_called_once_with(b"\x00" + bytes([0xA0, 0x03, 0x00, 0xA3]))


@patch("hid.Device")
def test_hid_on_ch8_8ch(mock_hid_cls):
    mock_dev = _make_hid_mock()
    mock_hid_cls.return_value = mock_dev

    with serve(NoyitoPowerHID(num_channels=8, channel=8)) as client:
        client.on()

    mock_dev.write.assert_called_once_with(b"\x00" + bytes([0xA0, 0x08, 0x01, 0xA9]))


@patch("hid.Device")
def test_hid_off_ch8_8ch(mock_hid_cls):
    mock_dev = _make_hid_mock()
    mock_hid_cls.return_value = mock_dev

    with serve(NoyitoPowerHID(num_channels=8, channel=8)) as client:
        client.off()

    mock_dev.write.assert_called_once_with(b"\x00" + bytes([0xA0, 0x08, 0x00, 0xA8]))


@patch("hid.Device")
def test_hid_all_channels_on_4ch(mock_hid_cls):
    mock_dev = _make_hid_mock()
    mock_hid_cls.return_value = mock_dev

    with serve(NoyitoPowerHID(num_channels=4, all_channels=True)) as client:
        client.on()

    assert mock_dev.write.call_count == 4
    write_calls = mock_dev.write.call_args_list
    assert write_calls[0] == call(b"\x00" + bytes([0xA0, 0x01, 0x01, 0xA2]))
    assert write_calls[1] == call(b"\x00" + bytes([0xA0, 0x02, 0x01, 0xA3]))
    assert write_calls[2] == call(b"\x00" + bytes([0xA0, 0x03, 0x01, 0xA4]))
    assert write_calls[3] == call(b"\x00" + bytes([0xA0, 0x04, 0x01, 0xA5]))


@patch("hid.Device")
def test_hid_read(mock_hid_cls):
    mock_dev = _make_hid_mock()
    mock_hid_cls.return_value = mock_dev

    with serve(NoyitoPowerHID(num_channels=4, channel=1)) as client:
        readings = list(client.read())

    assert len(readings) == 1
    assert readings[0].voltage == 0.0
    assert readings[0].current == 0.0


@patch("hid.Device")
def test_hid_cycle(mock_hid_cls):
    mock_dev = _make_hid_mock()
    mock_hid_cls.return_value = mock_dev

    with serve(NoyitoPowerHID(num_channels=4, channel=1)) as client:
        client.cycle(wait=0)

    write_calls = mock_dev.write.call_args_list
    assert write_calls[0] == call(b"\x00" + bytes([0xA0, 0x01, 0x00, 0xA1]))
    assert write_calls[1] == call(b"\x00" + bytes([0xA0, 0x01, 0x01, 0xA2]))
