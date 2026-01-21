import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jumpstarter_driver_pyserial.driver import PySerial

from .driver import RideSXDriver, RideSXPowerDriver
from jumpstarter.common.exceptions import ConfigurationError
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


@pytest.fixture(scope="session")
def ridesx_power_driver():
    yield RideSXPowerDriver(
        children={
            "serial": PySerial(url="loop://"),
        },
    )


# Configuration Tests


def test_missing_serial(temp_storage_dir):
    with pytest.raises(ConfigurationError):
        RideSXDriver(storage_dir=temp_storage_dir, children={})


# Fastboot Detection Tests


def test_detect_fastboot_device_found(ridesx_driver):
    with serve(ridesx_driver) as client:
        with patch("subprocess.run") as mock_subprocess:
            mock_result = MagicMock()
            mock_result.stdout = "ABC123456789    fastboot\n"
            mock_result.returncode = 0
            mock_subprocess.return_value = mock_result

            result = client.call("detect_fastboot_device", 1, 0.1)

            assert result["status"] == "device_found"
            assert result["device_id"] == "ABC123456789"
            mock_subprocess.assert_called_once()


def test_detect_fastboot_device_not_found(ridesx_driver):
    with serve(ridesx_driver) as client:
        with patch("subprocess.run") as mock_subprocess:
            mock_result = MagicMock()
            mock_result.stdout = ""
            mock_result.returncode = 0
            mock_subprocess.return_value = mock_result

            result = client.call("detect_fastboot_device", 2, 0.01)

            assert result["status"] == "no_device_found"
            assert result["device_id"] is None
            # Driver makes max_attempts calls plus one final attempt
            assert mock_subprocess.call_count >= 2


def test_detect_fastboot_device_timeout(ridesx_driver):
    with serve(ridesx_driver) as client:
        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = subprocess.TimeoutExpired("fastboot", 10)

            result = client.call("detect_fastboot_device", 2, 0.01)

            assert result["status"] == "no_device_found"
            assert result["device_id"] is None


def test_detect_fastboot_device_not_found_error(ridesx_driver):
    with serve(ridesx_driver) as client:
        with patch("subprocess.run", side_effect=FileNotFoundError("fastboot not found")):
            # When called through client, RuntimeError becomes DriverError
            from jumpstarter.client.core import DriverError

            with pytest.raises(DriverError, match="fastboot command not found"):
                client.call("detect_fastboot_device", 1, 0.1)


def test_detect_fastboot_device_retry_logic(ridesx_driver):
    with serve(ridesx_driver) as client:
        with patch("subprocess.run") as mock_subprocess:
            # First two attempts return empty, third returns device
            mock_results = [
                MagicMock(stdout="", returncode=0),
                MagicMock(stdout="", returncode=0),
                MagicMock(stdout="ABC123456789    fastboot\n", returncode=0),
            ]
            mock_subprocess.side_effect = mock_results

            result = client.call("detect_fastboot_device", 3, 0.01)

            assert result["status"] == "device_found"
            assert result["device_id"] == "ABC123456789"
            assert mock_subprocess.call_count == 3


# File Decompression Tests


def test_needs_decompression_gz(ridesx_driver):
    assert ridesx_driver._needs_decompression("file.gz") is True
    # Note: endswith is case-sensitive, so .GZ won't match
    # This is expected behavior - filenames should use lowercase extensions


def test_needs_decompression_xz(ridesx_driver):
    assert ridesx_driver._needs_decompression("file.xz") is True


def test_needs_decompression_gzip(ridesx_driver):
    assert ridesx_driver._needs_decompression("file.gzip") is True


def test_needs_decompression_no_compression(ridesx_driver):
    assert ridesx_driver._needs_decompression("file.img") is False
    assert ridesx_driver._needs_decompression("file.bin") is False
    assert ridesx_driver._needs_decompression("file") is False


def test_get_decompression_command(ridesx_driver):
    assert ridesx_driver._get_decompression_command("file.gz") == "zcat"
    assert ridesx_driver._get_decompression_command("file.gzip") == "zcat"
    assert ridesx_driver._get_decompression_command("file.xz") == "xzcat"
    assert ridesx_driver._get_decompression_command("file.img") == "cat"


def test_decompress_file_gz(temp_storage_dir, ridesx_driver):
    compressed_file = Path(temp_storage_dir) / "test.gz"
    compressed_file.write_bytes(b"compressed data")

    with patch("subprocess.run") as mock_subprocess:
        mock_result = MagicMock()
        mock_result.stderr = b""
        mock_result.returncode = 0
        mock_subprocess.return_value = mock_result

        # Mock the file existence and size check
        with patch.object(Path, "exists") as mock_exists, patch.object(Path, "stat") as mock_stat:
            mock_exists.return_value = True
            mock_stat.return_value = MagicMock(st_size=100)  # Non-zero size

            decompressed = ridesx_driver._decompress_file(compressed_file)

            assert decompressed.name == "test"
            mock_subprocess.assert_called_once()
            call_args = mock_subprocess.call_args
            assert call_args[0][0] == ["zcat", str(compressed_file)]


def test_decompress_file_xz(temp_storage_dir, ridesx_driver):
    compressed_file = Path(temp_storage_dir) / "test.xz"
    compressed_file.write_bytes(b"compressed data")

    with patch("subprocess.run") as mock_subprocess:
        mock_result = MagicMock()
        mock_result.stderr = b""
        mock_result.returncode = 0
        mock_subprocess.return_value = mock_result

        # Mock the file existence and size check
        with patch.object(Path, "exists") as mock_exists, patch.object(Path, "stat") as mock_stat:
            mock_exists.return_value = True
            mock_stat.return_value = MagicMock(st_size=100)  # Non-zero size

            decompressed = ridesx_driver._decompress_file(compressed_file)

            assert decompressed.name == "test"
            mock_subprocess.assert_called_once()
            call_args = mock_subprocess.call_args
            assert call_args[0][0] == ["xzcat", str(compressed_file)]


def test_decompress_file_failure(temp_storage_dir, ridesx_driver):
    compressed_file = Path(temp_storage_dir) / "test.gz"
    compressed_file.write_bytes(b"compressed data")

    with patch("subprocess.run") as mock_subprocess:
        mock_subprocess.side_effect = subprocess.CalledProcessError(1, "zcat", stderr=b"error")

        with pytest.raises(RuntimeError, match="failed to decompress"):
            ridesx_driver._decompress_file(compressed_file)


def test_decompress_file_timeout(temp_storage_dir, ridesx_driver):
    compressed_file = Path(temp_storage_dir) / "test.gz"
    compressed_file.write_bytes(b"compressed data")

    with patch("subprocess.run") as mock_subprocess:
        mock_subprocess.side_effect = subprocess.TimeoutExpired("zcat", 10)

        with pytest.raises(RuntimeError, match="decompression timeout"):
            ridesx_driver._decompress_file(compressed_file)


# Fastboot Flashing Tests


def test_flash_with_fastboot_single_partition(temp_storage_dir, ridesx_driver):
    # Create test image file
    image_file = Path(temp_storage_dir) / "boot.img"
    image_file.write_bytes(b"boot image data")

    with serve(ridesx_driver) as client:
        with patch("subprocess.run") as mock_subprocess:
            # Mock flash command
            flash_result = MagicMock()
            flash_result.stdout = "Flashing boot..."
            flash_result.stderr = ""
            flash_result.returncode = 0

            # Mock continue command
            continue_result = MagicMock()
            continue_result.stdout = "Continuing..."
            continue_result.stderr = ""
            continue_result.returncode = 0

            mock_subprocess.side_effect = [flash_result, continue_result]

            client.call("flash_with_fastboot", "ABC123", {"boot": "boot.img"})

            assert mock_subprocess.call_count == 2
            # Check flash command
            flash_call = mock_subprocess.call_args_list[0]
            assert flash_call[0][0] == ["fastboot", "-s", "ABC123", "flash", "boot", str(image_file)]
            # Check continue command
            continue_call = mock_subprocess.call_args_list[1]
            assert continue_call[0][0] == ["fastboot", "-s", "ABC123", "continue"]


def test_flash_with_fastboot_multiple_partitions(temp_storage_dir, ridesx_driver):
    # Create test image files
    boot_file = Path(temp_storage_dir) / "boot.img"
    boot_file.write_bytes(b"boot image data")
    system_file = Path(temp_storage_dir) / "system.img"
    system_file.write_bytes(b"system image data")

    with serve(ridesx_driver) as client:
        with patch("subprocess.run") as mock_subprocess:
            flash_result = MagicMock()
            flash_result.stdout = "Flashing..."
            flash_result.stderr = ""
            flash_result.returncode = 0

            continue_result = MagicMock()
            continue_result.stdout = "Continuing..."
            continue_result.stderr = ""
            continue_result.returncode = 0

            mock_subprocess.side_effect = [flash_result, flash_result, continue_result]

            client.call("flash_with_fastboot", "ABC123", {"boot": "boot.img", "system": "system.img"})

            assert mock_subprocess.call_count == 3
            # Verify both partitions were flashed
            flash_calls = [call[0][0] for call in mock_subprocess.call_args_list[:2]]
            assert ["fastboot", "-s", "ABC123", "flash", "boot", str(boot_file)] in flash_calls
            assert ["fastboot", "-s", "ABC123", "flash", "system", str(system_file)] in flash_calls


def test_flash_with_fastboot_compressed_file(temp_storage_dir, ridesx_driver):
    # Create compressed file
    compressed_file = Path(temp_storage_dir) / "boot.img.gz"
    compressed_file.write_bytes(b"compressed data")

    # Create decompressed file that will be "created" by decompression
    decompressed_file = Path(temp_storage_dir) / "boot.img"
    decompressed_file.write_bytes(b"decompressed data")

    with serve(ridesx_driver) as client:
        with patch.object(ridesx_driver, "_decompress_file", return_value=decompressed_file):
            with patch("subprocess.run") as mock_subprocess:
                flash_result = MagicMock()
                flash_result.stdout = "Flashing..."
                flash_result.stderr = ""
                flash_result.returncode = 0

                continue_result = MagicMock()
                continue_result.stdout = "Continuing..."
                continue_result.stderr = ""
                continue_result.returncode = 0

                mock_subprocess.side_effect = [flash_result, continue_result]

                client.call("flash_with_fastboot", "ABC123", {"boot": "boot.img.gz"})

                # Verify decompression was called
                ridesx_driver._decompress_file.assert_called_once_with(compressed_file)
                # Verify flash used decompressed file
                flash_call = mock_subprocess.call_args_list[0]
                assert str(decompressed_file) in flash_call[0][0]


def test_flash_with_fastboot_file_not_found(temp_storage_dir, ridesx_driver):
    with serve(ridesx_driver) as client:
        from jumpstarter.client.core import DriverError

        # When called through client, FileNotFoundError becomes DriverError
        with pytest.raises(DriverError, match="Image not found in storage"):
            client.call("flash_with_fastboot", "ABC123", {"boot": "nonexistent.img"})


def test_flash_with_fastboot_empty_partitions(ridesx_driver):
    with serve(ridesx_driver) as client:
        with pytest.raises(ValueError, match="At least one partition must be provided"):
            client.call("flash_with_fastboot", "ABC123", {})


def test_flash_with_fastboot_flash_failure(temp_storage_dir, ridesx_driver):
    image_file = Path(temp_storage_dir) / "boot.img"
    image_file.write_bytes(b"boot image data")

    with serve(ridesx_driver) as client:
        from jumpstarter.client.core import DriverError

        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = subprocess.CalledProcessError(1, "fastboot", stderr=b"flash failed")

            # When called through client, RuntimeError becomes DriverError
            with pytest.raises(DriverError, match="Failed to flash"):
                client.call("flash_with_fastboot", "ABC123", {"boot": "boot.img"})


def test_flash_with_fastboot_flash_timeout(temp_storage_dir, ridesx_driver):
    image_file = Path(temp_storage_dir) / "boot.img"
    image_file.write_bytes(b"boot image data")

    with serve(ridesx_driver) as client:
        from jumpstarter.client.core import DriverError

        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = subprocess.TimeoutExpired("fastboot", 20 * 60)

            # When called through client, RuntimeError becomes DriverError
            with pytest.raises(DriverError, match="timeout while flashing"):
                client.call("flash_with_fastboot", "ABC123", {"boot": "boot.img"})


def test_flash_with_fastboot_continue_success(temp_storage_dir, ridesx_driver):
    image_file = Path(temp_storage_dir) / "boot.img"
    image_file.write_bytes(b"boot image data")

    with serve(ridesx_driver) as client:
        with patch("subprocess.run") as mock_subprocess:
            flash_result = MagicMock()
            flash_result.stdout = "Flashing..."
            flash_result.stderr = ""
            flash_result.returncode = 0

            continue_result = MagicMock()
            continue_result.stdout = "Continuing..."
            continue_result.stderr = ""
            continue_result.returncode = 0

            mock_subprocess.side_effect = [flash_result, continue_result]

            client.call("flash_with_fastboot", "ABC123", {"boot": "boot.img"})

            # Verify continue was called
            continue_call = mock_subprocess.call_args_list[1]
            assert continue_call[0][0] == ["fastboot", "-s", "ABC123", "continue"]


def test_flash_with_fastboot_continue_failure(temp_storage_dir, ridesx_driver):
    image_file = Path(temp_storage_dir) / "boot.img"
    image_file.write_bytes(b"boot image data")

    with serve(ridesx_driver) as client:
        with patch("subprocess.run") as mock_subprocess:
            flash_result = MagicMock()
            flash_result.stdout = "Flashing..."
            flash_result.stderr = ""
            flash_result.returncode = 0

            # First call succeeds (flash), second call fails (continue)
            mock_subprocess.side_effect = [
                flash_result,
                subprocess.CalledProcessError(1, "fastboot", stderr=b"continue failed"),
            ]

            # Should not raise, just log warning
            client.call("flash_with_fastboot", "ABC123", {"boot": "boot.img"})

            # Verify both flash and continue were called
            assert mock_subprocess.call_count == 2


def test_power_missing_serial():
    with pytest.raises(ConfigurationError):
        RideSXPowerDriver(children={})


def test_power_on_exported(ridesx_power_driver):
    """Test that power on method is properly exported"""
    with serve(ridesx_power_driver):
        # Verify the method exists and is exported
        # Full execution requires proper serial responses
        assert hasattr(ridesx_power_driver, "on")
        import inspect

        assert inspect.iscoroutinefunction(ridesx_power_driver.on)


def test_power_off_exported(ridesx_power_driver):
    """Test that power off method is properly exported"""
    with serve(ridesx_power_driver):
        # Verify the method exists and is exported
        assert hasattr(ridesx_power_driver, "off")
        import inspect

        assert inspect.iscoroutinefunction(ridesx_power_driver.off)


@pytest.mark.asyncio
async def test_power_cycle(ridesx_power_driver):
    """Test power cycle calls off, waits, then on"""
    with patch.object(ridesx_power_driver, "off", new_callable=AsyncMock) as mock_off:
        with patch.object(ridesx_power_driver, "on", new_callable=AsyncMock) as mock_on:
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await ridesx_power_driver.cycle(delay=0.1)

                mock_off.assert_called_once()
                mock_on.assert_called_once()
                mock_sleep.assert_called_once_with(0.1)


def test_power_rescue(ridesx_power_driver):
    """Test that rescue raises NotImplementedError"""
    with serve(ridesx_power_driver) as client:
        with pytest.raises(NotImplementedError, match="Rescue mode not available"):
            client.call("rescue")

