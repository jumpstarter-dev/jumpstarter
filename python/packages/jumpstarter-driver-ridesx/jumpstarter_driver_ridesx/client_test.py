import tempfile
from unittest.mock import patch

import click
import pytest
from jumpstarter_driver_pyserial.driver import PySerial

from .driver import RideSXDriver
from jumpstarter.common.utils import serve


@pytest.fixture(scope="session")
def temp_storage_dir():
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir


@pytest.fixture(scope="session")
def ridesx_driver(temp_storage_dir):
    yield RideSXDriver(
        storage_dir=temp_storage_dir,
        children={
            "serial": PySerial(url="loop://"),
        },
    )


@pytest.fixture
def ridesx_client(ridesx_driver):
    """Create a client instance for testing client-side methods"""
    with serve(ridesx_driver) as client:
        yield client


# Validate Partition Mappings Tests


def test_validate_partition_mappings(ridesx_client):
    """Test partition mapping validation"""
    # None is valid (auto-detect mode)
    ridesx_client._validate_partition_mappings(None)

    # Valid mapping
    ridesx_client._validate_partition_mappings({"boot": "/path/to/boot.img"})

    # Empty path raises
    with pytest.raises(ValueError, match="has an empty file path"):
        ridesx_client._validate_partition_mappings({"boot": ""})

    # Whitespace-only path raises
    with pytest.raises(ValueError, match="has an empty file path"):
        ridesx_client._validate_partition_mappings({"boot": "   "})


# Flash OCI Auto Tests


def test_flash_oci_auto_success(ridesx_client):
    """Test successful flash_oci_auto call"""
    with patch.object(ridesx_client, "call") as mock_call:
        mock_call.side_effect = [
            None,  # boot_to_fastboot call
            {"status": "device_found", "device_id": "ABC123"},
            {"status": "success"},
        ]

        result = ridesx_client.flash_oci_auto("oci://quay.io/org/image:tag")

        assert result == {"status": "success"}
        # Verify flash_oci_image was called with the OCI URL
        flash_call = mock_call.call_args_list[2]
        assert flash_call[0][0] == "flash_oci_image"
        assert flash_call[0][1] == "oci://quay.io/org/image:tag"


def test_flash_oci_auto_error_cases(ridesx_client):
    """Test flash_oci_auto error handling"""
    # URL without oci:// scheme
    with pytest.raises(ValueError, match="OCI URL must start with oci://"):
        ridesx_client.flash_oci_auto("docker://image:tag")

    # Bare registry URL without oci:// prefix
    with pytest.raises(ValueError, match="OCI URL must start with oci://"):
        ridesx_client.flash_oci_auto("quay.io/org/image:tag")

    # No device found
    with patch.object(ridesx_client, "call") as mock_call:
        mock_call.return_value = {"status": "no_device_found", "device_id": None}

        with pytest.raises(click.ClickException, match="No fastboot devices found"):
            ridesx_client.flash_oci_auto("oci://image:tag")


# _execute_flash_command Tests


@pytest.mark.parametrize("invalid_path", [
    "boot_a:/path/to/boot.img",   # partition:absolute_path
    "boot_a:./boot_a.simg",       # partition:relative_path
    "boot_a:boot.img",            # partition:filename
    "./boot_a.simg",              # local file path
    "quay.io/org/image:tag",      # bare registry URL missing oci://
])
def test_execute_flash_command_rejects_non_oci_positional_with_targets(ridesx_client, invalid_path):
    """Non-oci:// positional paths should be rejected in multi-target mode"""
    with pytest.raises(click.ClickException, match="missing the -t flag"):
        ridesx_client._execute_flash_command(
            invalid_path,
            ("system_a:/path/to/system.img",),
        )


def test_execute_flash_command_error_shows_all_target_specs(ridesx_client):
    """Error example should include all -t specs, not just the first"""
    with pytest.raises(click.ClickException) as exc_info:
        ridesx_client._execute_flash_command(
            "boot_a:boot.img",
            ("system_a:/path/to/system.img", "vendor_a:/path/to/vendor.img"),
        )
    msg = str(exc_info.value)
    assert "-t system_a:/path/to/system.img" in msg
    assert "-t vendor_a:/path/to/vendor.img" in msg


def test_execute_flash_command_allows_oci_positional_with_targets(ridesx_client):
    """OCI positional paths should pass the guard in multi-target mode"""
    with patch.object(ridesx_client, "flash_with_targets") as mock_flash:
        ridesx_client._execute_flash_command(
            "oci://quay.io/org/image:tag",
            ("boot_a:boot.img",),
        )
        mock_flash.assert_called_once_with(
            "oci://quay.io/org/image:tag",
            {"boot_a": "boot.img"},
            power_off=True,
        )


# flash() partition:path hint Tests


def test_flash_hints_partition_spec_without_target(ridesx_client):
    """Passing partition:path directly to flash() should give a helpful error"""
    # Absolute path after colon
    with pytest.raises(click.ClickException, match="looks like a partition:path mapping"):
        ridesx_client.flash("boot_a:/path/to/boot.img")

    # Bare filename after colon
    with pytest.raises(click.ClickException, match="looks like a partition:path mapping"):
        ridesx_client.flash("boot_a:boot.img")


def test_flash_hints_oci_missing_prefix(ridesx_client):
    """Bare registry URL without oci:// should suggest adding the prefix"""
    with pytest.raises(click.ClickException, match="OCI URLs must start with oci://") as exc_info:
        ridesx_client.flash("quay.io/org/image:tag")
    assert "oci://quay.io/org/image:tag" in str(exc_info.value)


def test_flash_no_target_no_partition_spec(ridesx_client):
    """Non-OCI path without colon or target should give a generic helpful error"""
    with pytest.raises(click.ClickException, match="requires a target partition"):
        ridesx_client.flash("/path/to/boot.img")


def test_upload_file_if_needed_strips_query_params(ridesx_client):
    """Verify _upload_file_if_needed produces a clean filename for signed URLs"""
    from jumpstarter_driver_opendal.client import clean_filename

    # Simulate the path_buf that would come from operator_for_path with a signed URL
    path_with_query = "/images/image.raw.xz?Expires=123&Signature=abc/def&Key-Pair-Id=xyz"
    result = clean_filename(path_with_query)
    assert result == "image.raw.xz"

    # Also verify the direct path case
    result = clean_filename("/images/image.raw.xz")
    assert result == "image.raw.xz"
