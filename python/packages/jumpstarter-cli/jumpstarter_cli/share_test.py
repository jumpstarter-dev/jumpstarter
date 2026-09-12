import inspect
from unittest.mock import Mock, patch

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
    lease_entry.shared_with = ["alice", "bob"]
    lease_entry.effective_shared_with = ["alice", "bob"]

    config = Mock()
    config.get_lease.return_value = lease_entry

    inspect.unwrap(share_list.callback)(config=config, lease="my-lease")

    config.get_lease.assert_called_once_with("my-lease")
    captured = capsys.readouterr()
    assert "my-lease" in captured.out
    assert "alice" in captured.out
    assert "bob" in captured.out
    # Both are effective, so neither should be flagged as inactive.
    assert "not active" not in captured.out


def test_share_list_flags_denied_clients(capsys):
    # bob is in the owner's desired intent but was denied by policy / not found,
    # so it must be rendered as inactive while alice (effective) is not.
    lease_entry = Mock()
    lease_entry.shared_with = ["alice", "bob"]
    lease_entry.effective_shared_with = ["alice"]

    config = Mock()
    config.get_lease.return_value = lease_entry

    inspect.unwrap(share_list.callback)(config=config, lease="my-lease")

    captured = capsys.readouterr()
    lines = {line.strip().split("  ")[0]: line for line in captured.out.splitlines() if line.startswith("  ")}
    assert "not active" in lines["bob"]
    assert "not active" not in lines["alice"]


def test_share_list_not_shared(capsys):
    lease_entry = Mock()
    lease_entry.shared_with = []

    config = Mock()
    config.get_lease.return_value = lease_entry

    inspect.unwrap(share_list.callback)(config=config, lease="my-lease")

    captured = capsys.readouterr()
    assert "not shared" in captured.out
