import inspect
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import anyio
import click
import pytest

from jumpstarter_cli.shell import _resolve_lease_from_active_async, _shell_with_signal_handling, shell

from jumpstarter.client.grpc import Lease, LeaseList
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.env import JMP_LEASE


def _make_lease(name: str, client: str = "test-client") -> Lease:
    return Lease(
        namespace="default",
        name=name,
        selector="",
        exporter_name=None,
        duration=timedelta(minutes=30),
        effective_duration=None,
        begin_time=datetime.now(),
        client=client,
        exporter="test-exporter",
        conditions=[],
        effective_begin_time=None,
        effective_end_time=None,
    )


def _make_lease_list(names: list[str]) -> LeaseList:
    return LeaseList(
        leases=[_make_lease(n) for n in names],
        next_page_token=None,
    )


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

    with patch(
        "jumpstarter_cli.shell._run_shell_with_lease_async",
        new=AsyncMock(return_value=0),
    ):
        exit_code = anyio.run(
            _shell_with_signal_handling,
            config,
            None,
            "laptop-test-exporter",
            None,
            timedelta(minutes=1),
            False,
            (),
            None,
        )

    assert exit_code == 0
    assert config.captured is not None
    assert config.captured[1] == "laptop-test-exporter"


def test_shell_requires_selector_or_name_when_no_leases():
    config = Mock(spec=ClientConfigV1Alpha1)
    config.metadata = type("Metadata", (), {"name": "test-client"})()
    config.list_leases = AsyncMock(return_value=_make_lease_list([]))
    with pytest.raises(click.UsageError, match="no active leases found"):
        shell.callback.__wrapped__.__wrapped__(
            config=config,
            command=(),
            lease_name=None,
            selector=None,
            exporter_name=None,
            duration=timedelta(minutes=1),
            exporter_logs=False,
            acquisition_timeout=None,
            tls_grpc_address=None,
            tls_grpc_insecure=False,
            passphrase=None,
        )


def test_shell_allows_existing_lease_name_without_selector_or_name():
    with (
        patch("jumpstarter_cli.shell.anyio.run", return_value=0),
        patch("jumpstarter_cli.shell.sys.exit") as mock_exit,
    ):
        inspect.unwrap(shell.callback)(
            config=Mock(spec=ClientConfigV1Alpha1),
            command=(),
            lease_name="existing-lease",
            selector=None,
            exporter_name=None,
            duration=timedelta(minutes=1),
            exporter_logs=False,
            acquisition_timeout=None,
            tls_grpc_address=None,
            tls_grpc_insecure=False,
            passphrase=None,
        )

    mock_exit.assert_called_once_with(0)


def test_shell_auto_connects_single_lease():
    config = Mock(spec=ClientConfigV1Alpha1)
    config.metadata = type("Metadata", (), {"name": "test-client"})()
    with (
        patch("jumpstarter_cli.shell.anyio.run", side_effect=["my-only-lease", 0]) as mock_run,
        patch("jumpstarter_cli.shell.sys.exit") as mock_exit,
    ):
        shell.callback.__wrapped__.__wrapped__(
            config=config,
            command=(),
            lease_name=None,
            selector=None,
            exporter_name=None,
            duration=timedelta(minutes=1),
            exporter_logs=False,
            acquisition_timeout=None,
            tls_grpc_address=None,
            tls_grpc_insecure=False,
            passphrase=None,
        )

    resolve_call_args = mock_run.call_args_list[0]
    assert resolve_call_args[0][0] is _resolve_lease_from_active_async
    assert resolve_call_args[0][1] is config
    shell_call_args = mock_run.call_args_list[1]
    assert shell_call_args[0][4] == "my-only-lease"
    mock_exit.assert_called_once_with(0)


def test_shell_no_leases_shows_guidance():
    config = Mock(spec=ClientConfigV1Alpha1)
    config.metadata = type("Metadata", (), {"name": "test-client"})()
    config.list_leases = AsyncMock(return_value=_make_lease_list([]))
    with pytest.raises(click.UsageError, match="no active leases found"):
        shell.callback.__wrapped__.__wrapped__(
            config=config,
            command=(),
            lease_name=None,
            selector=None,
            exporter_name=None,
            duration=timedelta(minutes=1),
            exporter_logs=False,
            acquisition_timeout=None,
            tls_grpc_address=None,
            tls_grpc_insecure=False,
            passphrase=None,
        )
    config.list_leases.assert_called_once_with(only_active=True)


def test_shell_multi_lease_tty_picker():
    config = Mock(spec=ClientConfigV1Alpha1)
    config.metadata = type("Metadata", (), {"name": "test-client"})()
    config.list_leases = AsyncMock(return_value=_make_lease_list(["lease-a", "lease-b", "lease-c"]))
    with (
        patch("jumpstarter_cli.shell.sys.stdin") as mock_stdin,
        patch("jumpstarter_cli.shell.click.prompt", return_value=2),
    ):
        mock_stdin.isatty.return_value = True
        selected = anyio.run(_resolve_lease_from_active_async, config)

    assert selected == "lease-b"
    config.list_leases.assert_called_once_with(only_active=True)


def test_shell_multi_lease_no_tty_error():
    config = Mock(spec=ClientConfigV1Alpha1)
    config.metadata = type("Metadata", (), {"name": "test-client"})()
    config.list_leases = AsyncMock(return_value=_make_lease_list(["lease-a", "lease-b"]))
    with (
        patch("jumpstarter_cli.shell.sys.stdin") as mock_stdin,
        pytest.raises(click.UsageError, match="lease-a"),
    ):
        mock_stdin.isatty.return_value = False
        shell.callback.__wrapped__.__wrapped__(
            config=config,
            command=(),
            lease_name=None,
            selector=None,
            exporter_name=None,
            duration=timedelta(minutes=1),
            exporter_logs=False,
            acquisition_timeout=None,
            tls_grpc_address=None,
            tls_grpc_insecure=False,
            passphrase=None,
        )


def test_shell_filters_leases_by_current_client():
    other_user_lease = _make_lease("other-user-lease", client="other-client")
    my_lease = _make_lease("my-lease", client="test-client")
    lease_list = LeaseList(leases=[other_user_lease, my_lease], next_page_token=None)
    config = Mock(spec=ClientConfigV1Alpha1)
    config.metadata = type("Metadata", (), {"name": "test-client"})()
    config.list_leases = AsyncMock(return_value=lease_list)

    selected = anyio.run(_resolve_lease_from_active_async, config)
    assert selected == "my-lease"
    config.list_leases.assert_called_once_with(only_active=True)


def test_shell_no_own_leases_among_others():
    other_lease = _make_lease("other-lease", client="other-client")
    lease_list = LeaseList(leases=[other_lease], next_page_token=None)
    config = Mock(spec=ClientConfigV1Alpha1)
    config.metadata = type("Metadata", (), {"name": "test-client"})()
    config.list_leases = AsyncMock(return_value=lease_list)
    with pytest.raises(click.UsageError, match="no active leases found"):
        shell.callback.__wrapped__.__wrapped__(
            config=config,
            command=(),
            lease_name=None,
            selector=None,
            exporter_name=None,
            duration=timedelta(minutes=1),
            exporter_logs=False,
            acquisition_timeout=None,
            tls_grpc_address=None,
            tls_grpc_insecure=False,
            passphrase=None,
        )


def test_shell_allows_env_lease_without_selector_or_name():
    with (
        patch("jumpstarter_cli.shell.anyio.run", return_value=0),
        patch("jumpstarter_cli.shell.sys.exit") as mock_exit,
        patch.dict("os.environ", {JMP_LEASE: "existing-lease"}, clear=False),
    ):
        inspect.unwrap(shell.callback)(
            config=Mock(spec=ClientConfigV1Alpha1),
            command=(),
            lease_name=None,
            selector=None,
            exporter_name=None,
            duration=timedelta(minutes=1),
            exporter_logs=False,
            acquisition_timeout=None,
            tls_grpc_address=None,
            tls_grpc_insecure=False,
            passphrase=None,
        )

    mock_exit.assert_called_once_with(0)

def test_resolve_lease_handles_async_list_leases():
    config = Mock(spec=ClientConfigV1Alpha1)
    config.metadata = type("Metadata", (), {"name": "test-client"})()
    config.list_leases = AsyncMock(return_value=_make_lease_list(["async-lease"]))

    selected = anyio.run(_resolve_lease_from_active_async, config)
    assert selected == "async-lease"
    config.list_leases.assert_called_once_with(only_active=True)
