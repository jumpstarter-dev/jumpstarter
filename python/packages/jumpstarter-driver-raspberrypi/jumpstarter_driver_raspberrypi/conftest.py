import sys
from unittest.mock import MagicMock


def pytest_configure(config):
    mock_lgpio = MagicMock()
    mock_lgpio.SET_OPEN_DRAIN = 4
    mock_lgpio._pin_state = {}

    def mock_gpiochip_open(chip_num):
        return 0

    def mock_gpiochip_close(handle):
        pass

    def mock_gpio_claim_output(handle, pin, initial_value, flags=0):
        pass

    def mock_gpio_claim_input(handle, pin):
        pass

    def mock_gpio_free(handle, pin):
        if (handle, pin) in mock_lgpio._pin_state:
            del mock_lgpio._pin_state[(handle, pin)]

    def mock_gpio_read(handle, pin):
        return mock_lgpio._pin_state.get((handle, pin), 0)

    def mock_gpio_write(handle, pin, value):
        mock_lgpio._pin_state[(handle, pin)] = value

    mock_lgpio.gpiochip_open = mock_gpiochip_open
    mock_lgpio.gpiochip_close = mock_gpiochip_close
    mock_lgpio.gpio_claim_output = mock_gpio_claim_output
    mock_lgpio.gpio_claim_input = mock_gpio_claim_input
    mock_lgpio.gpio_free = mock_gpio_free
    mock_lgpio.gpio_read = mock_gpio_read
    mock_lgpio.gpio_write = mock_gpio_write
    sys.modules["lgpio"] = mock_lgpio
