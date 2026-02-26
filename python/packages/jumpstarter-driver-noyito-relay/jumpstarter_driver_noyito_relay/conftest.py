import sys
from unittest.mock import MagicMock

# Stub the hid module so tests run without the native hidapi shared library.
# NoyitoPowerHID._send_command defers `import hid` to call time; this stub
# ensures that deferred import returns a mock rather than attempting to load
# the native library.  Tests that verify HID commands patch hid.Device
# explicitly on top of this stub.
if "hid" not in sys.modules:
    sys.modules["hid"] = MagicMock()

import pytest
import serial


def pytest_runtest_call(item):
    try:
        item.runtest()
    except serial.SerialException:
        pytest.skip("Serial device not available")  # ty: ignore[call-non-callable]
