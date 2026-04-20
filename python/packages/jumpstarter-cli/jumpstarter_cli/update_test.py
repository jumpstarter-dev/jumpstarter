import inspect
from datetime import timedelta
from unittest.mock import Mock, patch

import click
import pytest

from jumpstarter_cli.update import update_lease


def test_update_lease_with_to_client():
    config = Mock()
    config.metadata.namespace = "test-ns"
    lease = Mock()
    config.update_lease.return_value = lease

    assert update_lease.callback is not None
    with patch("jumpstarter_cli.update.model_print") as model_print:
        inspect.unwrap(update_lease.callback)(
            config=config,
            name="my-lease",
            duration=None,
            begin_time=None,
            to_client="other-client",
            output="yaml",
        )

    config.update_lease.assert_called_once_with(
        "my-lease",
        duration=None,
        begin_time=None,
        client="namespaces/test-ns/clients/other-client",
    )
    model_print.assert_called_once_with(lease, "yaml")


def test_update_lease_with_duration_and_to_client():
    config = Mock()
    config.metadata.namespace = "test-ns"
    lease = Mock()
    config.update_lease.return_value = lease

    assert update_lease.callback is not None
    with patch("jumpstarter_cli.update.model_print") as model_print:
        inspect.unwrap(update_lease.callback)(
            config=config,
            name="my-lease",
            duration=timedelta(hours=2),
            begin_time=None,
            to_client="other-client",
            output="yaml",
        )

    config.update_lease.assert_called_once_with(
        "my-lease",
        duration=timedelta(hours=2),
        begin_time=None,
        client="namespaces/test-ns/clients/other-client",
    )
    model_print.assert_called_once_with(lease, "yaml")


def test_update_lease_without_to_client():
    config = Mock()
    lease = Mock()
    config.update_lease.return_value = lease

    assert update_lease.callback is not None
    with patch("jumpstarter_cli.update.model_print") as model_print:
        inspect.unwrap(update_lease.callback)(
            config=config,
            name="my-lease",
            duration=timedelta(hours=1),
            begin_time=None,
            to_client=None,
            output="yaml",
        )

    config.update_lease.assert_called_once_with(
        "my-lease",
        duration=timedelta(hours=1),
        begin_time=None,
        client=None,
    )
    model_print.assert_called_once_with(lease, "yaml")


def test_update_lease_requires_at_least_one_option():
    assert update_lease.callback is not None
    with pytest.raises(click.UsageError, match="At least one of"):
        inspect.unwrap(update_lease.callback)(
            config=Mock(),
            name="my-lease",
            duration=None,
            begin_time=None,
            to_client=None,
            output="yaml",
        )
