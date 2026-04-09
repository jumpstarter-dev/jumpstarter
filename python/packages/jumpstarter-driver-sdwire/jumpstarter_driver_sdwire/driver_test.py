import pytest
import usb

from jumpstarter_driver_sdwire.driver import SDWire

from jumpstarter.common.utils import serve


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
