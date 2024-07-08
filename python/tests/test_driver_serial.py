from jumpstarter.drivers.serial import MockSerial, PySerial
import serial
import pytest
import os


@pytest.mark.skipif(not os.path.exists("/dev/ttyUSB0"), reason="no serial port")
def test_pyserial_serial():
    p = PySerial(serial.Serial("/dev/ttyUSB0"))

    assert p.call("write", [b"hello"]) == 5
    assert p.call("read", [5]) == b"hello"


def test_mock_serial():
    p = MockSerial(labels={"jumpstarter.dev/name": "mock"})

    assert p.call("write", [b"hello"]) == 5
    assert p.call("read", [5]) == b"hello"
