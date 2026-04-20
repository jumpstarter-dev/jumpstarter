import inspect
from datetime import timedelta
from unittest.mock import Mock, patch

import click
import pytest

from jumpstarter_cli.create import create_lease


def test_create_lease_passes_exporter_name_to_config():
    config = Mock()
    lease = Mock()
    config.create_lease.return_value = lease

    assert create_lease.callback is not None
    with patch("jumpstarter_cli.create.model_print") as model_print:
        # Skip Click config loading wrapper and call the command body directly.
        inspect.unwrap(create_lease.callback)(
            config=config,
            selector=None,
            exporter_name="laptop-test-exporter",
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            output="yaml",
        )

    config.create_lease.assert_called_once_with(
        selector=None,
        exporter_name="laptop-test-exporter",
        duration=timedelta(minutes=5),
        begin_time=None,
        lease_id=None,
    )
    model_print.assert_called_once_with(lease, "yaml")


def test_create_lease_requires_selector_or_name():
    assert create_lease.callback is not None
    with pytest.raises(click.UsageError, match="one of --selector/-l or --name/-n is required"):
        inspect.unwrap(create_lease.callback)(
            config=Mock(),
            selector=None,
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            output="yaml",
        )
