import pytest
import usb


def pytest_runtest_call(item):
    try:
        item.runtest()
    except FileNotFoundError:
        pytest.skip("yepkit not available")  # ty: ignore[too-many-positional-arguments]
    except usb.core.USBError:
        pytest.skip("USB not available, could need root permissions")  # ty: ignore[too-many-positional-arguments]
    except usb.core.NoBackendError:
        pytest.skip("No USB backend")  # ty: ignore[too-many-positional-arguments]
