from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from jumpstarter_driver_raspberrypi.driver import DigitalInput, DigitalOutput, PowerSwitch

from jumpstarter.common.utils import serve


def _mock_gpiod():
    """Create a mock gpiod module for testing"""
    mock_gpiod = Mock()

    # Mock enums
    mock_gpiod.line = Mock()
    mock_gpiod.line.Direction = Mock()
    mock_gpiod.line.Direction.OUTPUT = "OUTPUT"
    mock_gpiod.line.Direction.INPUT = "INPUT"
    mock_gpiod.line.Drive = Mock()
    mock_gpiod.line.Drive.PUSH_PULL = "PUSH_PULL"
    mock_gpiod.line.Drive.OPEN_DRAIN = "OPEN_DRAIN"
    mock_gpiod.line.Drive.OPEN_SOURCE = "OPEN_SOURCE"
    mock_gpiod.line.Value = Mock()
    mock_gpiod.line.Value.ACTIVE = "ACTIVE"
    mock_gpiod.line.Value.INACTIVE = "INACTIVE"
    mock_gpiod.line.Edge = Mock()
    mock_gpiod.line.Edge.BOTH = "BOTH"

    # Mock EdgeEvent
    mock_gpiod.EdgeEvent = Mock()
    mock_gpiod.EdgeEvent.Type = Mock()
    mock_gpiod.EdgeEvent.Type.RISING_EDGE = "RISING_EDGE"
    mock_gpiod.EdgeEvent.Type.FALLING_EDGE = "FALLING_EDGE"

    # Mock LineSettings
    mock_gpiod.LineSettings = Mock()

    # Mock Chip and LineRequest
    mock_chip = Mock()
    mock_request = Mock()
    mock_gpiod.Chip = Mock(return_value=mock_chip)
    mock_chip.request_lines = Mock(return_value=mock_request)

    # Store pin states for testing
    mock_gpiod._pin_states = {}

    # Mock get_value to return stored pin states
    def mock_get_value(pin):
        return mock_gpiod._pin_states.get(pin, "INACTIVE")

    mock_request.get_value = Mock(side_effect=mock_get_value)

    # Mock set_value to store pin states
    def mock_set_value(pin, value):
        mock_gpiod._pin_states[pin] = value

    mock_request.set_value = Mock(side_effect=mock_set_value)

    # Mock edge event waiting
    mock_request.wait_edge_events = Mock(return_value=True)
    mock_request.read_edge_events = Mock(return_value=[])

    return mock_gpiod


@patch('jumpstarter_driver_raspberrypi.driver.gpiod')
def test_drivers_gpio_digital_output(mock_gpiod_module):
    mock_gpiod = _mock_gpiod()
    mock_gpiod_module.__dict__.update(mock_gpiod.__dict__)

    pin_number = 1
    instance = DigitalOutput(pin=pin_number, mode="push_pull")

    assert mock_gpiod._pin_states.get(pin_number, "INACTIVE") == "INACTIVE"

    with serve(instance) as client:
        client.off()  # type: ignore[attr-defined]
        assert mock_gpiod._pin_states.get(pin_number) == "INACTIVE"

        client.on()  # type: ignore[attr-defined]
        assert mock_gpiod._pin_states.get(pin_number) == "ACTIVE"

        client.off()  # type: ignore[attr-defined]
        assert mock_gpiod._pin_states.get(pin_number) == "INACTIVE"


@patch('jumpstarter_driver_raspberrypi.driver.gpiod')
def test_drivers_gpio_digital_input(mock_gpiod_module):
    mock_gpiod = _mock_gpiod()
    mock_gpiod_module.__dict__.update(mock_gpiod.__dict__)

    pin_number = 4
    instance = DigitalInput(pin=pin_number)

    # Set initial pin state
    mock_gpiod._pin_states[pin_number] = "INACTIVE"

    with serve(instance) as client:
        # Test wait_for_active
        with ThreadPoolExecutor() as pool:
            fut = pool.submit(client.wait_for_active, timeout=1.0)  # type: ignore[attr-defined]
            # Simulate pin becoming active
            mock_gpiod._pin_states[pin_number] = "ACTIVE"
            # Mock edge event for rising edge
            mock_event = Mock()
            mock_event.line_offset = pin_number
            mock_event.event_type = "RISING_EDGE"
            mock_gpiod._request.read_edge_events.return_value = [mock_event]
            fut.result(timeout=1.5)

        # Test wait_for_inactive
        with ThreadPoolExecutor() as pool:
            fut = pool.submit(client.wait_for_inactive, timeout=1.0)  # type: ignore[attr-defined]
            # Simulate pin becoming inactive
            mock_gpiod._pin_states[pin_number] = "INACTIVE"
            # Mock edge event for falling edge
            mock_event = Mock()
            mock_event.line_offset = pin_number
            mock_event.event_type = "FALLING_EDGE"
            mock_gpiod._request.read_edge_events.return_value = [mock_event]
            fut.result(timeout=1.5)


@patch('jumpstarter_driver_raspberrypi.driver.gpiod')
def test_drivers_gpio_power_switch_open_drain(mock_gpiod_module):
    mock_gpiod = _mock_gpiod()
    mock_gpiod_module.__dict__.update(mock_gpiod.__dict__)

    pin_number = 2
    instance = PowerSwitch(pin=pin_number, mode="open_drain")

    assert mock_gpiod._pin_states.get(pin_number, "INACTIVE") == "INACTIVE"

    with serve(instance) as client:
        client.off()  # type: ignore[attr-defined]
        assert mock_gpiod._pin_states.get(pin_number) == "INACTIVE"

        client.on()  # type: ignore[attr-defined]
        assert mock_gpiod._pin_states.get(pin_number) == "ACTIVE"

        client.off()  # type: ignore[attr-defined]
        assert mock_gpiod._pin_states.get(pin_number) == "INACTIVE"

        client.cycle()  # type: ignore[attr-defined]
        assert mock_gpiod._pin_states.get(pin_number) == "ACTIVE"


@patch('jumpstarter_driver_raspberrypi.driver.gpiod')
def test_drivers_gpio_power_switch_push_pull(mock_gpiod_module):
    mock_gpiod = _mock_gpiod()
    mock_gpiod_module.__dict__.update(mock_gpiod.__dict__)

    pin_number = 3
    instance = PowerSwitch(pin=pin_number, mode="push_pull")

    assert mock_gpiod._pin_states.get(pin_number, "INACTIVE") == "INACTIVE"

    with serve(instance) as client:
        client.off()  # type: ignore[attr-defined]
        assert mock_gpiod._pin_states.get(pin_number) == "INACTIVE"

        client.on()  # type: ignore[attr-defined]
        assert mock_gpiod._pin_states.get(pin_number) == "ACTIVE"

        client.off()  # type: ignore[attr-defined]
        assert mock_gpiod._pin_states.get(pin_number) == "INACTIVE"
