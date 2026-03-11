from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import Mock, patch

import anyio
import click
import pytest

from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter_cli.shell import _shell_with_signal_handling, shell


class _DummyConfig:
    def __init__(self):
        self.captured = None
        self.token = None

    @asynccontextmanager
    async def lease_async(self, selector, exporter_name, lease_name, duration, portal, acquisition_timeout):
        self.captured = (selector, exporter_name, lease_name, duration, acquisition_timeout)
        yield Mock()


def test_shell_passes_exporter_name_to_lease_async():
    config = _DummyConfig()

    with patch("jumpstarter_cli.shell._run_shell_with_lease", return_value=0):
        exit_code = anyio.run(
            _shell_with_signal_handling,
            config,
            None,
            "laptop-test-exporter",
            None,
            timedelta(minutes=1),
            False,
            tuple(),
            None,
        )

    assert exit_code == 0
    assert config.captured is not None
    assert config.captured[1] == "laptop-test-exporter"


def test_shell_requires_selector_or_name():
    with pytest.raises(click.UsageError, match="one of --selector/-l or --name/-n is required"):
        shell.callback.__wrapped__.__wrapped__(
            config=Mock(spec=ClientConfigV1Alpha1),
            command=tuple(),
            lease_name=None,
            selector=None,
            exporter_name=None,
            duration=timedelta(minutes=1),
            exporter_logs=False,
            acquisition_timeout=None,
        )
