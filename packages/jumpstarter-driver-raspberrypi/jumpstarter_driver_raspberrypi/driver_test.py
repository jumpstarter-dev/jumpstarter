

from jumpstarter.common.utils import serve


def test_drivers_gpio_digital_input(monkeypatch):
    monkeypatch.setenv("BLINKA_OS_AGNOSTIC", "1")

    from digitalio import Pull

    from jumpstarter_driver_raspberrypi.driver import DigitalIO

    with serve(DigitalIO(pin="Dx_INPUT_TOGGLE")) as client:
        client.switch_to_input(pull=Pull.UP)
        assert client.value
        assert not client.value
        assert client.value


def test_drivers_gpio_digital_output(monkeypatch):
    monkeypatch.setenv("BLINKA_OS_AGNOSTIC", "1")

    from digitalio import DriveMode

    from jumpstarter_driver_raspberrypi.driver import DigitalIO

    with serve(DigitalIO(pin="Dx_OUTPUT")) as client:
        client.switch_to_output(value=True, drive_mode=DriveMode.PUSH_PULL)
        client.value = True
        assert client.value
        client.value = False
        # Dx_OUTPUT is always True
        assert client.value


def test_drivers_gpio_power(monkeypatch):
    monkeypatch.setenv("BLINKA_OS_AGNOSTIC", "1")

    from jumpstarter_driver_raspberrypi.driver import DigitalPowerButton, DigitalPowerSwitch

    with serve(DigitalPowerSwitch(pin="Dx_OUTPUT", drive_mode="PUSH_PULL")) as client:
        client.off()
        client.on()

    with serve(DigitalPowerButton(pin="Dx_OUTPUT", drive_mode="PUSH_PULL", off_press_seconds=1)) as client:
        client.off()
        client.on()
