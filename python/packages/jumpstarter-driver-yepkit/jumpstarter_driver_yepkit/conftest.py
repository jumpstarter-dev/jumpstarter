import pytest
import usb


def pytest_runtest_call(item):
    try:
        item.runtest()
    except FileNotFoundError:
        pytest.skip("yepkit not available") # ty: ignore[call-non-callable]
    except usb.core.USBError:
        pytest.skip("USB not available, could need root permissions") # ty: ignore[call-non-callable]
    except usb.core.NoBackendError:
        pytest.skip("No USB backend") # ty: ignore[call-non-callable]
