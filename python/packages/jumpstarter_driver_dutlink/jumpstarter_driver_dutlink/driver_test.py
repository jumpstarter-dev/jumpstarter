import pytest
import usb
from jumpstarter_driver_network.adapters import PexpectAdapter

from jumpstarter.common.utils import serve
from jumpstarter_driver_dutlink.driver import Dutlink


def test_drivers_dutlink():
    try:
        instance = Dutlink(
            storage_device="/dev/null",
        )
    except FileNotFoundError:
        pytest.skip("dutlink not available")
    except usb.core.USBError:
        pytest.skip("USB not available")
    except usb.core.NoBackendError:
        pytest.skip("No USB backend")

    with serve(instance) as client:
        with PexpectAdapter(client=client.console) as expect:
            expect.send("\x02" * 5)

            expect.send("about\r\n")
            expect.expect("Jumpstarter test-harness")

            expect.send("console\r\n")
            expect.expect("Entering console mode")

            client.power.off()

            client.storage.write_local_file("/dev/null")
            client.storage.dut()

            client.power.on()

            expect.send("\x02" * 5)
            expect.expect("Exiting console mode")

            client.power.off()
