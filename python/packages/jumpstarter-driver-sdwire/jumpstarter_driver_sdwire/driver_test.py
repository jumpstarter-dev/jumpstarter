import pytest
import usb
from jumpstarter.common.utils import serve

from jumpstarter_driver_sdwire.driver import SDWire


def test_drivers_sdwire():
    try:
        instance = SDWire()
    except FileNotFoundError:
        pytest.skip("sd-wire not available")
    except usb.core.USBError:
        pytest.skip("USB not available")
    except usb.core.NoBackendError:
        pytest.skip("No USB backend")

    with serve(instance) as client:
        client.host()
        assert instance.query() == "host"
        client.dut()
        assert instance.query() == "dut"
