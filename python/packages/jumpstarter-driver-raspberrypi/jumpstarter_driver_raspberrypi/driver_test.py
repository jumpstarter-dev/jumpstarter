from concurrent.futures import ThreadPoolExecutor

from jumpstarter_driver_raspberrypi.driver import DigitalInput, DigitalOutput, PowerSwitch, lgpio

from jumpstarter.common.utils import serve


def _pin_state(handle: int, pin: int) -> int:
    """Helper to fetch a pin's logic level from the lgpio"""

    return lgpio._pin_state.get((handle, pin), 0)  # type: ignore[attr-defined]


def test_drivers_gpio_digital_output():
    pin_number = 1
    instance = DigitalOutput(pin=pin_number)

    assert _pin_state(0, pin_number) == 0

    with serve(instance) as client:
        client.off()  # type: ignore[attr-defined]
        assert _pin_state(0, pin_number) == 0

        client.on()  # type: ignore[attr-defined]
        assert _pin_state(0, pin_number) == 1

        client.off()  # type: ignore[attr-defined]
        assert _pin_state(0, pin_number) == 0


def test_drivers_gpio_digital_input():
    pin_number = 4
    instance = DigitalInput(pin=pin_number)

    lgpio._pin_state[(0, pin_number)] = 0  # type: ignore[attr-defined]

    with serve(instance) as client:
        with ThreadPoolExecutor() as pool:
            fut = pool.submit(client.wait_for_active, timeout=1.0)  # type: ignore[attr-defined]
            lgpio._pin_state[(0, pin_number)] = 1  # type: ignore[attr-defined]
            fut.result(timeout=1.5)

        with ThreadPoolExecutor() as pool:
            fut = pool.submit(client.wait_for_inactive, timeout=1.0)  # type: ignore[attr-defined]
            lgpio._pin_state[(0, pin_number)] = 0  # type: ignore[attr-defined]
            fut.result(timeout=1.5)


def test_drivers_gpio_power_switch_open_drain():
    pin_number = 2
    instance = PowerSwitch(pin=pin_number, open_drain=True)

    assert _pin_state(0, pin_number) == 0

    with serve(instance) as client:
        client.off()  # type: ignore[attr-defined]
        assert _pin_state(0, pin_number) == 0

        client.on()  # type: ignore[attr-defined]
        assert _pin_state(0, pin_number) == 1

        client.off()  # type: ignore[attr-defined]
        assert _pin_state(0, pin_number) == 0

        client.cycle()  # type: ignore[attr-defined]
        assert _pin_state(0, pin_number) == 1
