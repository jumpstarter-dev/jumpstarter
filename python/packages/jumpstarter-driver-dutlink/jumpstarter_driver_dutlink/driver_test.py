from time import sleep

from jumpstarter_driver_network.adapters import PexpectAdapter

from jumpstarter_driver_dutlink.driver import Dutlink, DutlinkPower, DutlinkSerial, DutlinkStorageMux

from jumpstarter.common.utils import serve

STORAGE_DEVICE = "/dev/null"  # MANUAL: replace with path to block device


def power_test(power):
    # Test normal power on/off sequence
    power.on()  # MANUAL: led DUT_ON should be turned on
    sleep(1)
    assert next(power.read()).current != 0
    power.off()  # MANUAL: led DUT_ON should be turned off

    # Test rescue mode
    power.rescue()  # MANUAL: device should enter rescue mode
    sleep(1)
    # Note: We can't assert the state here as rescue mode behavior is device-specific
    power.off()  # MANUAL: device should power off after rescue mode


def storage_test(storage):
    storage.flash("/dev/null")


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
    instance = DutlinkPower()

    with serve(instance) as client:
        power_test(client)


def test_drivers_dutlink_storage_mux():
    instance = DutlinkStorageMux(storage_device=STORAGE_DEVICE)

    with serve(instance) as client:
        storage_test(client)


def test_drivers_dutlink_serial():
    instance = DutlinkSerial()  # MANUAL: connect tx to rx

    with serve(instance) as client:
        serial_test(client)


def test_drivers_dutlink():
    instance = Dutlink(storage_device=STORAGE_DEVICE)

    with serve(instance) as client:
        power_test(client.power)
        storage_test(client.storage)
        serial_test(client.console)
