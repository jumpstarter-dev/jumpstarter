import pytest
import usb


def pytest_runtest_call(item):
    try:
        item.runtest()
    except FileNotFoundError:
        pytest.skip("dutlink not available")  # ty: ignore[call-non-callable]
    except usb.core.USBError:
        pytest.skip("USB not available")  # ty: ignore[call-non-callable]
    except usb.core.NoBackendError:
        pytest.skip("No USB backend")  # ty: ignore[call-non-callable]
