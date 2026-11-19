import platform
from unittest.mock import PropertyMock, patch

import pytest
from pydantic import SecretStr

from jumpstarter_driver_qemu.client import QemuClient
from jumpstarter_driver_qemu.driver import Qemu

from jumpstarter.common.oci import OciCredentials
from jumpstarter.common.utils import serve


@pytest.fixture
def qemu_client():
    """Serve a QEMU driver and yield the composite client for client-side tests."""
    with serve(Qemu()) as client:
        yield client


def _skip_without_ssh_on_macos(qemu_client) -> None:
    """A bare Qemu() exports ssh only when vhost-vsock is available."""
    if platform.system() == "Darwin" and "ssh" not in qemu_client.children:
        pytest.skip("ssh child is absent on macOS")  # ty: ignore[too-many-positional-arguments]


def test_flash_oci_forwards_authenticated_credentials(qemu_client):
    """The client must resolve OCI creds locally and forward them to the exporter's
       flash_oci call.
    """
    creds = OciCredentials(username="myuser", password=SecretStr("mypass"))
    with (
        patch("jumpstarter.common.oci.resolve_oci_credentials", return_value=creds),
        patch.object(qemu_client.flasher, "streamingcall", return_value=iter([])) as mock_sc,
    ):
        qemu_client.flasher.flash("oci://quay.io/private/image:tag")

    mock_sc.assert_called_once()
    args = mock_sc.call_args.args
    assert args[0] == "flash_oci"
    assert args[1] == "oci://quay.io/private/image:tag"
    # flash_oci signature: (oci_url, partition, oci_username, oci_password)
    assert args[3] == "myuser"
    assert args[4] == "mypass"


def test_flash_oci_forwards_none_when_unauthenticated(qemu_client):
    """Without client-side creds, forward None/None so the exporter falls back
    to its own env/auth-file resolution (backward compatible)."""
    with (
        patch("jumpstarter.common.oci.resolve_oci_credentials", return_value=OciCredentials()),
        patch.object(qemu_client.flasher, "streamingcall", return_value=iter([])) as mock_sc,
    ):
        qemu_client.flasher.flash("oci://quay.io/public/image:tag")

    mock_sc.assert_called_once()
    args = mock_sc.call_args.args
    assert args[0] == "flash_oci"
    assert args[3] is None
    assert args[4] is None


def test_shell_disables_agent_when_password_is_set(qemu_client):
    """A configured guest password must not fall through to the SSH agent."""
    _skip_without_ssh_on_macos(qemu_client)
    with patch("jumpstarter_driver_qemu.client.FabricAdapter") as adapter:
        adapter.return_value.__enter__.return_value = "conn"
        with qemu_client.shell() as conn:
            assert conn == "conn"

    assert adapter.call_args.kwargs["user"] == "jumpstarter"
    assert adapter.call_args.kwargs["connect_kwargs"] == {
        "password": "password",
        "allow_agent": False,
        "look_for_keys": False,
    }


def test_shell_keeps_key_auth_when_password_is_empty(qemu_client):
    """An empty password keeps agent and discovered-key authentication."""
    _skip_without_ssh_on_macos(qemu_client)
    with (
        patch.object(QemuClient, "password", new_callable=PropertyMock, return_value=""),
        patch("jumpstarter_driver_qemu.client.FabricAdapter") as adapter,
    ):
        adapter.return_value.__enter__.return_value = "conn"
        with qemu_client.shell():
            pass

    assert adapter.call_args.kwargs["connect_kwargs"] == {"password": ""}
