from concurrent.futures import ThreadPoolExecutor

from gpiozero import Device
from gpiozero.pins.mock import MockFactory

from jumpstarter_driver_raspberrypi.driver import DigitalInput, DigitalOutput

from jumpstarter.common.utils import serve

Device.pin_factory = MockFactory()


def test_drivers_gpio_digital_output():
    pin_factory = MockFactory()
    Device.pin_factory = pin_factory
    pin_number = 1
    mock_pin = pin_factory.pin(pin_number)

    instance = DigitalOutput(pin=pin_number)

    assert not mock_pin.state

    with serve(instance) as client:
        client.off()
        assert not mock_pin.state

        client.on()
        assert mock_pin.state

        client.off()
        assert not mock_pin.state

    mock_pin.assert_states([False, True, False])


def test_drivers_gpio_digital_input():
    instance = DigitalInput(pin=4)

    with serve(instance) as client:
        with ThreadPoolExecutor() as pool:
            pool.submit(client.wait_for_active)
            instance.device.pin.drive_high()

        with ThreadPoolExecutor() as pool:
            pool.submit(client.wait_for_inactive)
            instance.device.pin.drive_low()
