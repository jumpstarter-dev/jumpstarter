import inspect
import logging
from contextlib import contextmanager
from unittest.mock import MagicMock, call, patch

import pexpect
import pytest

from .client import UbootConsoleClient
from .common import ESC


def test_reboot_to_console_accepts_retries_kwarg() -> None:
    sig = inspect.signature(UbootConsoleClient.reboot_to_console)
    retries_param = sig.parameters.get("retries")
    assert retries_param is not None
    assert retries_param.default == 100
    assert retries_param.kind == inspect.Parameter.KEYWORD_ONLY


def test_reboot_to_console_retries_limits_attempts() -> None:
    mock_power = MagicMock()
    mock_pexpect_process = MagicMock()
    mock_pexpect_process.send = MagicMock()
    mock_pexpect_process.expect_exact = MagicMock(side_effect=pexpect.TIMEOUT("timeout"))

    @contextmanager
    def fake_pexpect():
        yield mock_pexpect_process

    mock_serial = MagicMock()
    mock_serial.pexpect = fake_pexpect

    client = object.__new__(UbootConsoleClient)
    client.children = {"power": mock_power, "serial": mock_serial}
    client.logger = logging.getLogger("test_uboot")

    prompt_value = "=> "
    with patch.object(type(client), "prompt", new_callable=lambda: property(lambda self: prompt_value)):
        with pytest.raises(RuntimeError, match="Failed to get U-Boot prompt"):
            with client.reboot_to_console(retries=3):
                pass

    assert mock_pexpect_process.send.call_count == 3
    mock_pexpect_process.send.assert_has_calls([call(ESC)] * 3)
    mock_power.cycle.assert_called_once()
