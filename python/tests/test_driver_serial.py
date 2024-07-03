from jumpstarter.drivers.serial.pyserial import PySerial
from jumpstarter.drivers.serial.mock import MockSerial
import serial
import pytest
import os


@pytest.mark.skipif(not os.path.exists("/dev/ttyUSB0"), reason="no serial port")
def test_pyserial_serial():
    p = PySerial(serial.Serial("/dev/ttyUSB0"))

    assert p.call("write", [b"hello"]) == 5
    assert p.call("read", [5]) == b"hello"
    assert p.call("set_baudrate", [115200]) == None
    assert p.call("get_baudrate", []) == 115200

def test_mock_serial():
    p = MockSerial()

    assert p.call("write", [b"hello"]) == 5
    assert p.call("read", [5]) == b"hello"
    assert p.call("set_baudrate", [115200]) == None
    assert p.call("get_baudrate", []) == 115200
