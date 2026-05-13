import inspect

import pytest

from .client import UbootConsoleClient


def test_reboot_to_console_accepts_retries_kwarg() -> None:
    sig = inspect.signature(UbootConsoleClient.reboot_to_console)
    retries_param = sig.parameters.get("retries")
    assert retries_param is not None
    assert retries_param.default == 100
    assert retries_param.kind == inspect.Parameter.KEYWORD_ONLY


def test_reboot_to_console_retries_default_is_100() -> None:
    sig = inspect.signature(UbootConsoleClient.reboot_to_console)
    assert sig.parameters["retries"].default == 100
