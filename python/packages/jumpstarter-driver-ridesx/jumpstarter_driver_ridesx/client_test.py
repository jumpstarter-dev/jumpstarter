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
            {"status": "device_found", "device_id": "ABC123"},
            {"status": "success"},
        ]

        result = ridesx_client.flash_oci_auto("oci://quay.io/org/image:tag")

        assert result == {"status": "success"}
        # Verify flash_oci_image was called with the OCI URL
        flash_call = mock_call.call_args_list[1]
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
