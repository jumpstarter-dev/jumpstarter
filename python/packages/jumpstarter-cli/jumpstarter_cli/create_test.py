from datetime import timedelta
from unittest.mock import Mock, patch

import click
import pytest

from jumpstarter_cli.create import create_lease


def test_create_lease_passes_exporter_name_to_config():
    config = Mock()
    lease = Mock()
    config.create_lease.return_value = lease

    with patch("jumpstarter_cli.create.model_print") as model_print:
        # Skip Click config loading wrapper and call the command body directly.
        create_lease.callback.__wrapped__.__wrapped__(
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
    with pytest.raises(click.UsageError, match="one of --selector/-l or --name/-n is required"):
        create_lease.callback.__wrapped__.__wrapped__(
            config=Mock(),
            selector=None,
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            output="yaml",
        )
