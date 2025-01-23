from time import sleep

import pytest

from jumpstarter_driver_dutlink.driver import Dutlink, DutlinkPower, DutlinkSerial, DutlinkStorageMux

from jumpstarter.client.adapters import PexpectAdapter
from jumpstarter.common.utils import serve

STORAGE_DEVICE = "/dev/null"  # MANUAL: replace with path to block device


def power_test(power):
    power.on()  # MANUAL: led DUT_ON should be turned on
    sleep(1)
    assert next(power.read()).current != 0
    power.off()  # MANUAL: led DUT_ON should be turned off


def storage_test(storage):
    storage.write_local_file("/dev/null")


def serial_test(serial):
    with PexpectAdapter(client=serial) as expect:
        expect.send("\x02" * 5)

        expect.send("about\r\n")
        expect.expect("Jumpstarter test-harness")

        expect.send("console\r\n")
        expect.expect("Entering console mode")

        expect.send("hello")
        expect.expect("hello")


def test_drivers_dutlink_power():
    try:
        instance = DutlinkPower()
    except Exception:
        pytest.skip("dutlink not available")

    with serve(instance) as client:
        power_test(client)


def test_drivers_dutlink_storage_mux():
    try:
        instance = DutlinkStorageMux(storage_device=STORAGE_DEVICE)
    except Exception:
        pytest.skip("dutlink not available")

    with serve(instance) as client:
        storage_test(client)


def test_drivers_dutlink_serial():
    try:
        instance = DutlinkSerial()  # MANUAL: connect tx to rx
    except Exception:
        pytest.skip("dutlink not available")

    with serve(instance) as client:
        serial_test(client)


def test_drivers_dutlink():
    try:
        instance = Dutlink(storage_device=STORAGE_DEVICE)
    except FileNotFoundError:
        pytest.skip("dutlink not available")

    with serve(instance) as client:
        power_test(client.power)
        storage_test(client.storage)
        serial_test(client.console)
