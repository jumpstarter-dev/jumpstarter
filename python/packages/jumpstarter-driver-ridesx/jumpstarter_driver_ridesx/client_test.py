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
    with pytest.raises(ValueError, match="Only oci:// URLs are supported"):
        ridesx_client.flash_oci_auto("docker://image:tag")

    # Invalid URL format
    with pytest.raises(ValueError, match="Invalid OCI URL format"):
        ridesx_client.flash_oci_auto("invalid-url")

    # No device found
    with patch.object(ridesx_client, "call") as mock_call:
        mock_call.return_value = {"status": "no_device_found", "device_id": None}

        with pytest.raises(click.ClickException, match="No fastboot devices found"):
            ridesx_client.flash_oci_auto("oci://image:tag")


# _is_oci_path Tests


def test_is_oci_path_rejects_partition_specs(ridesx_client):
    """Partition:path specs should not be detected as OCI references"""
    assert not ridesx_client._is_oci_path("boot_a:./boot_a.simg")
    assert not ridesx_client._is_oci_path("boot_a:/path/to/boot_a.simg")
    assert not ridesx_client._is_oci_path("system_a:../images/system.img")
    assert not ridesx_client._is_oci_path("boot_a:~/images/boot.img")


def test_is_oci_path_accepts_oci_references(ridesx_client):
    """Valid OCI references should be detected"""
    assert ridesx_client._is_oci_path("oci://quay.io/org/image:tag")
    assert ridesx_client._is_oci_path("quay.io/org/image:tag")
    assert ridesx_client._is_oci_path("registry.com/repo:latest")


def test_is_oci_path_rejects_non_oci(ridesx_client):
    """Non-OCI paths should not be detected"""
    assert not ridesx_client._is_oci_path("/absolute/path/to/file.img")
    assert not ridesx_client._is_oci_path("./relative/path")
    assert not ridesx_client._is_oci_path("http://example.com/file.img")
    assert not ridesx_client._is_oci_path("https://example.com/file.img")
    assert not ridesx_client._is_oci_path("just-a-filename")


# _execute_flash_command Tests


def test_execute_flash_command_detects_missing_t_flag(ridesx_client):
    """When a partition:path is passed as positional arg with other -t specs, suggest -t"""
    with pytest.raises(click.ClickException, match="missing the -t flag"):
        ridesx_client._execute_flash_command(
            "boot_a:/path/to/boot.img",
            ("system_a:/path/to/system.img",),
        )


def test_execute_flash_command_detects_missing_t_flag_relative(ridesx_client):
    """Relative partition:path should also be caught"""
    with pytest.raises(click.ClickException, match="missing the -t flag"):
        ridesx_client._execute_flash_command(
            "boot_a:./boot_a.simg",
            ("system_a:./system_a.simg",),
        )


def test_execute_flash_command_rejects_non_oci_positional_with_targets(ridesx_client):
    """Non-OCI positional paths should be rejected in multi-target mode"""
    # partition:filename without path prefix (previously slipped through)
    with pytest.raises(click.ClickException, match="not an OCI reference"):
        ridesx_client._execute_flash_command(
            "boot_a:boot.img",
            ("system_a:/path/to/system.img",),
        )

    # Local file path without colon
    with pytest.raises(click.ClickException, match="not an OCI reference"):
        ridesx_client._execute_flash_command(
            "./boot_a.simg",
            ("system_a:/path/to/system.img",),
        )

    # Plain filename
    with pytest.raises(click.ClickException, match="not an OCI reference"):
        ridesx_client._execute_flash_command(
            "boot.img",
            ("system_a:/path/to/system.img",),
        )


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
    """Passing partition:/path directly to flash() should give a helpful error"""
    with pytest.raises(ValueError, match="looks like a partition:path mapping"):
        ridesx_client.flash("boot_a:/path/to/boot.img")
