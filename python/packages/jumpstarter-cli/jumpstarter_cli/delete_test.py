from datetime import timedelta
from unittest.mock import Mock, call

import click
import pytest

from jumpstarter_cli.delete import delete_leases

from jumpstarter.client.grpc import Lease, LeaseList


def _make_lease(name, client="my-client"):
    return Lease(
        namespace="default",
        name=name,
        selector="",
        exporter_name=None,
        duration=timedelta(minutes=30),
        effective_duration=None,
        begin_time=None,
        client=client,
        exporter="test-exporter",
        conditions=[],
        effective_begin_time=None,
        effective_end_time=None,
    )


def _make_config(leases):
    config = Mock()
    config.metadata = type("Metadata", (), {"name": "my-client"})()
    config.list_leases = Mock(return_value=LeaseList(leases=leases, next_page_token=None))
    config.delete_lease = Mock()
    return config


_delete_leases = delete_leases.callback.__wrapped__.__wrapped__


def test_delete_all_only_deletes_own_leases():
    my_lease = _make_lease("my-lease", client="my-client")
    other_lease = _make_lease("other-lease", client="other-client")
    config = _make_config([my_lease, other_lease])

    _delete_leases(
        config=config, names=(), selector=None,
        delete_all=True, all_clients=False, output=None,
    )

    config.delete_lease.assert_called_once_with(name="my-lease")


def test_delete_all_deletes_multiple_own_leases():
    leases = [
        _make_lease("lease-1", client="my-client"),
        _make_lease("lease-2", client="my-client"),
        _make_lease("lease-3", client="other-client"),
    ]
    config = _make_config(leases)

    _delete_leases(
        config=config, names=(), selector=None,
        delete_all=True, all_clients=False, output=None,
    )

    assert config.delete_lease.call_count == 2
    config.delete_lease.assert_has_calls(
        [call(name="lease-1"), call(name="lease-2")],
        any_order=False,
    )


def test_delete_all_clients_deletes_everyones_leases():
    leases = [
        _make_lease("my-lease", client="my-client"),
        _make_lease("other-lease", client="other-client"),
    ]
    config = _make_config(leases)

    _delete_leases(
        config=config, names=(), selector=None,
        delete_all=False, all_clients=True, output=None,
    )

    assert config.delete_lease.call_count == 2
    config.delete_lease.assert_has_calls(
        [call(name="my-lease"), call(name="other-lease")],
        any_order=False,
    )


def test_delete_all_and_all_clients():
    leases = [
        _make_lease("my-lease", client="my-client"),
        _make_lease("other-lease", client="other-client"),
    ]
    config = _make_config(leases)

    _delete_leases(
        config=config, names=(), selector=None,
        delete_all=True, all_clients=True, output=None,
    )

    assert config.delete_lease.call_count == 2


def test_delete_by_selector_only_deletes_own_leases():
    my_lease = _make_lease("my-lease", client="my-client")
    my_lease.selector = "env=test"
    other_lease = _make_lease("other-lease", client="other-client")
    other_lease.selector = "env=test"
    lease_list = LeaseList(leases=[my_lease, other_lease], next_page_token=None)
    config = _make_config([])
    config.list_leases = Mock(return_value=lease_list)

    _delete_leases(
        config=config, names=(), selector="env=test",
        delete_all=False, all_clients=False, output=None,
    )

    config.delete_lease.assert_called_once_with(name="my-lease")


def test_delete_by_selector_with_all_clients():
    my_lease = _make_lease("my-lease", client="my-client")
    my_lease.selector = "env=test"
    other_lease = _make_lease("other-lease", client="other-client")
    other_lease.selector = "env=test"
    lease_list = LeaseList(leases=[my_lease, other_lease], next_page_token=None)
    config = _make_config([])
    config.list_leases = Mock(return_value=lease_list)

    _delete_leases(
        config=config, names=(), selector="env=test",
        delete_all=False, all_clients=True, output=None,
    )

    assert config.delete_lease.call_count == 2


def test_delete_by_name_deletes_specified_lease():
    config = _make_config([])

    _delete_leases(
        config=config, names=("specific-lease",), selector=None,
        delete_all=False, all_clients=False, output=None,
    )

    config.delete_lease.assert_called_once_with(name="specific-lease")


def test_delete_multiple_names():
    config = _make_config([])

    _delete_leases(
        config=config, names=("lease-1", "lease-2", "lease-3"), selector=None,
        delete_all=False, all_clients=False, output=None,
    )

    assert config.delete_lease.call_count == 3
    config.delete_lease.assert_has_calls(
        [call(name="lease-1"), call(name="lease-2"), call(name="lease-3")],
        any_order=False,
    )


def test_delete_no_args_raises_error():
    config = _make_config([])

    with pytest.raises(
        click.ClickException,
        match="One of NAMES, --selector, --all or --all-clients must be specified",
    ):
        _delete_leases(
            config=config, names=(), selector=None,
            delete_all=False, all_clients=False, output=None,
        )


def test_delete_all_raises_error_when_no_leases_match():
    config = _make_config([])

    with pytest.raises(
        click.ClickException,
        match="no leases found matching the criteria",
    ):
        _delete_leases(
            config=config, names=(), selector=None,
            delete_all=True, all_clients=False, output=None,
        )


def test_delete_all_clients_raises_error_when_no_leases_match():
    config = _make_config([])

    with pytest.raises(
        click.ClickException,
        match="no leases found matching the criteria",
    ):
        _delete_leases(
            config=config, names=(), selector=None,
            delete_all=False, all_clients=True, output=None,
        )


def test_delete_by_selector_raises_error_when_no_leases_match():
    config = _make_config([])

    with pytest.raises(
        click.ClickException,
        match="no leases found matching the criteria",
    ):
        _delete_leases(
            config=config, names=(), selector="env=missing",
            delete_all=False, all_clients=False, output=None,
        )
