import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from unittest.mock import MagicMock, patch

import pexpect
import pytest
from jumpstarter_driver_power.driver import MockPower
from jumpstarter_driver_pyserial.driver import PySerial

from .client import UbootConsoleClient
from .driver import UbootConsole
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver


def test_reboot_to_console_yields_on_prompt_match() -> None:
    mock_power = MagicMock()
    mock_pexpect_process = MagicMock()
    mock_pexpect_process.send = MagicMock()
    mock_pexpect_process.expect_exact = MagicMock(
        side_effect=[pexpect.TIMEOUT("timeout"), pexpect.TIMEOUT("timeout"), None]
    )

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
        entered = False
        with client.reboot_to_console(retries=5):
            entered = True

    assert entered
    assert mock_pexpect_process.send.call_count == 3
    mock_power.cycle.assert_called_once()


def test_reboot_to_console_retries_zero_raises_immediately() -> None:
    mock_power = MagicMock()
    mock_pexpect_process = MagicMock()

    @contextmanager
    def fake_pexpect():
        yield mock_pexpect_process

    mock_serial = MagicMock()
    mock_serial.pexpect = fake_pexpect

    client = object.__new__(UbootConsoleClient)
    client.children = {"power": mock_power, "serial": mock_serial}
    client.logger = logging.getLogger("test_uboot")

    prompt_value = "=> "
    with (
        patch.object(type(client), "prompt", new_callable=lambda: property(lambda self: prompt_value)),
        pytest.raises(RuntimeError, match="Failed to get U-Boot prompt"),client.reboot_to_console(retries=0)
    ):
        pass

    mock_pexpect_process.send.assert_not_called()
    mock_power.cycle.assert_called_once()


@pytest.mark.parametrize(
    ("config", "kwargs", "window"),
    [
        ({"prompt_timeout": 0.5}, {}, 0.5),
        ({"prompt_timeout": 60}, {"retries": 3}, 0.3),
    ],
)
def test_reboot_to_console_waits_for_the_prompt_until_the_window_ends(monkeypatch, config, kwargs, window) -> None:
    """loop:// echoes ESC back and never shows a prompt, so only the end of the window stops the wait."""
    console = UbootConsole(children={"power": MockPower(), "serial": PySerial(url="loop://")}, **config)
    with serve(console) as client:
        monkeypatch.setattr(client.power, "cycle", partial(client.power.cycle, wait=0))
        start = time.monotonic()
        with (
            pytest.raises(RuntimeError, match=rf"Failed to get U-Boot prompt within {window:g}s"),
            client.reboot_to_console(**kwargs),
        ):
            pass
        elapsed = time.monotonic() - start

    assert window <= elapsed < window + 2


@dataclass(kw_only=True)
class UbootConsoleWithoutPromptTimeout(Driver):
    """The exporter side of UbootConsole before get_prompt_timeout existed"""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_uboot.client.UbootConsoleClient"


def test_prompt_timeout_is_15s_on_exporters_without_the_setting() -> None:
    with serve(UbootConsoleWithoutPromptTimeout()) as client:
        assert client.prompt_timeout == 15
