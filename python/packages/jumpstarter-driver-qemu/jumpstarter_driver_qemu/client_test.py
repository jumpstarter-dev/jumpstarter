from unittest.mock import patch

import pytest
from pydantic import SecretStr

from jumpstarter_driver_qemu.driver import Qemu

from jumpstarter.common.oci import OciCredentials
from jumpstarter.common.utils import serve


@pytest.fixture
def qemu_client():
    """Serve a QEMU driver and yield the composite client for client-side tests."""
    with serve(Qemu()) as client:
        yield client


def test_flash_oci_forwards_authenticated_credentials(qemu_client):
    """The client must resolve OCI creds locally and forward them to the exporter's
       flash_oci call.
    """
    creds = OciCredentials(username="myuser", password=SecretStr("mypass"))
    with patch("jumpstarter.common.oci.resolve_oci_credentials", return_value=creds):
        with patch.object(qemu_client.flasher, "streamingcall", return_value=iter([])) as mock_sc:
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
    with patch("jumpstarter.common.oci.resolve_oci_credentials", return_value=OciCredentials()):
        with patch.object(qemu_client.flasher, "streamingcall", return_value=iter([])) as mock_sc:
            qemu_client.flasher.flash("oci://quay.io/public/image:tag")

    mock_sc.assert_called_once()
    args = mock_sc.call_args.args
    assert args[0] == "flash_oci"
    assert args[3] is None
    assert args[4] is None
