import inspect
from unittest.mock import Mock, patch

import click
import pytest

from jumpstarter_cli.share import share_add, share_list, share_remove


def test_share_add_calls_update_lease():
    config = Mock()
    updated = Mock()
    config.update_lease.return_value = updated

    with patch("jumpstarter_cli.share.model_print") as model_print:
        inspect.unwrap(share_add.callback)(
            config=config,
            lease="my-lease",
            clients=("alice", "bob"),
            output="yaml",
        )

    config.update_lease.assert_called_once_with("my-lease", add_shared_with=["alice", "bob"])
    model_print.assert_called_once_with(updated, "yaml")


def test_share_remove_calls_update_lease():
    config = Mock()
    updated = Mock()
    config.update_lease.return_value = updated

    with patch("jumpstarter_cli.share.model_print") as model_print:
        inspect.unwrap(share_remove.callback)(
            config=config,
            lease="my-lease",
            clients=("alice",),
            output="yaml",
        )

    config.update_lease.assert_called_once_with("my-lease", remove_shared_with=["alice"])
    model_print.assert_called_once_with(updated, "yaml")


def test_share_list_shows_shared_clients(capsys):
    lease_entry = Mock()
    lease_entry.name = "my-lease"
    lease_entry.shared_with = ["alice", "bob"]

    leases_result = Mock()
    leases_result.leases = [lease_entry]

    config = Mock()
    config.list_leases.return_value = leases_result

    inspect.unwrap(share_list.callback)(config=config, lease="my-lease")

    config.list_leases.assert_called_once_with(only_active=True)
    captured = capsys.readouterr()
    assert "my-lease" in captured.out
    assert "alice" in captured.out
    assert "bob" in captured.out


def test_share_list_not_found():
    leases_result = Mock()
    leases_result.leases = []

    config = Mock()
    config.list_leases.return_value = leases_result

    with pytest.raises(click.ClickException, match="not found"):
        inspect.unwrap(share_list.callback)(config=config, lease="no-such-lease")


def test_share_list_not_shared(capsys):
    lease_entry = Mock()
    lease_entry.name = "my-lease"
    lease_entry.shared_with = []

    leases_result = Mock()
    leases_result.leases = [lease_entry]

    config = Mock()
    config.list_leases.return_value = leases_result

    inspect.unwrap(share_list.callback)(config=config, lease="my-lease")

    captured = capsys.readouterr()
    assert "not shared" in captured.out
