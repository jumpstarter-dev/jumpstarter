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

    with patch("jumpstarter_cli.create.model_print") as model_print:
        # Skip Click config loading wrapper and call the command body directly.
        inspect.unwrap(create_lease.callback)(
            config=config,
            selector=None,
            exporter_name="laptop-test-exporter",
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=(),
            output="yaml",
        )

    config.create_lease.assert_called_once_with(
        selector=None,
        exporter_name="laptop-test-exporter",
        duration=timedelta(minutes=5),
        begin_time=None,
        lease_id=None,
        tags=None,
    )
    model_print.assert_called_once_with(lease, "yaml")


def test_create_lease_requires_selector_or_name():
    with pytest.raises(click.UsageError, match="one of --selector/-l or --name/-n is required"):
        inspect.unwrap(create_lease.callback)(
            config=Mock(),
            selector=None,
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=(),
            output="yaml",
        )


def test_create_lease_passes_tags_to_config():
    config = Mock()
    lease = Mock()
    config.create_lease.return_value = lease

    with patch("jumpstarter_cli.create.model_print"):
        inspect.unwrap(create_lease.callback)(
            config=config,
            selector="board=rpi4",
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=("team=devops", "ci-job=12345"),
            output="yaml",
        )

    config.create_lease.assert_called_once_with(
        selector="board=rpi4",
        exporter_name=None,
        duration=timedelta(minutes=5),
        begin_time=None,
        lease_id=None,
        tags={"team": "devops", "ci-job": "12345"},
    )


def test_create_lease_empty_tags_passes_none():
    config = Mock()
    lease = Mock()
    config.create_lease.return_value = lease

    with patch("jumpstarter_cli.create.model_print"):
        inspect.unwrap(create_lease.callback)(
            config=config,
            selector="board=rpi4",
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=(),
            output="yaml",
        )

    config.create_lease.assert_called_once_with(
        selector="board=rpi4",
        exporter_name=None,
        duration=timedelta(minutes=5),
        begin_time=None,
        lease_id=None,
        tags=None,
    )


def test_create_lease_invalid_tag_format():
    with pytest.raises(click.UsageError, match="Invalid tag format"):
        inspect.unwrap(create_lease.callback)(
            config=Mock(),
            selector="board=rpi4",
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=("invalid-no-equals",),
            output="yaml",
        )
