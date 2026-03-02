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


def _make_ok_result(stdout="OK"):
    r = MagicMock()
    r.stdout = stdout
    r.stderr = ""
    r.returncode = 0
    return r


def _set_active_results():
    """Two successful results for _reset_active_slot (set_active b, set_active a)."""
    return [_make_ok_result("set_active b"), _make_ok_result("set_active a")]


def test_flash_with_fastboot_single_partition(temp_storage_dir, ridesx_driver):
    image_file = Path(temp_storage_dir) / "boot.img"
    image_file.write_bytes(b"boot image data")

    with serve(ridesx_driver) as client:
        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = [
                *_set_active_results(),
                _make_ok_result("Flashing boot..."),
                _make_ok_result("Continuing..."),
            ]

            client.call("flash_with_fastboot", "ABC123", {"boot": "boot.img"})

            assert mock_subprocess.call_count == 4
            assert mock_subprocess.call_args_list[0][0][0] == ["fastboot", "-s", "ABC123", "set_active", "b"]
            assert mock_subprocess.call_args_list[1][0][0] == ["fastboot", "-s", "ABC123", "set_active", "a"]
            assert mock_subprocess.call_args_list[2][0][0] == [
                "fastboot", "-s", "ABC123", "flash", "boot", str(image_file),
            ]
            assert mock_subprocess.call_args_list[3][0][0] == ["fastboot", "-s", "ABC123", "continue"]


def test_flash_with_fastboot_multiple_partitions(temp_storage_dir, ridesx_driver):
    boot_file = Path(temp_storage_dir) / "boot.img"
    boot_file.write_bytes(b"boot image data")
    system_file = Path(temp_storage_dir) / "system.img"
    system_file.write_bytes(b"system image data")

    with serve(ridesx_driver) as client:
        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = [
                *_set_active_results(),
                _make_ok_result("Flashing boot..."),
                _make_ok_result("Flashing system..."),
                _make_ok_result("Continuing..."),
            ]

            client.call("flash_with_fastboot", "ABC123", {"boot": "boot.img", "system": "system.img"})

            assert mock_subprocess.call_count == 5
            flash_calls = [call[0][0] for call in mock_subprocess.call_args_list[2:4]]
            assert ["fastboot", "-s", "ABC123", "flash", "boot", str(boot_file)] in flash_calls
            assert ["fastboot", "-s", "ABC123", "flash", "system", str(system_file)] in flash_calls


def test_flash_with_fastboot_compressed_file(temp_storage_dir, ridesx_driver):
    compressed_file = Path(temp_storage_dir) / "boot.img.gz"
    compressed_file.write_bytes(b"compressed data")
    decompressed_file = Path(temp_storage_dir) / "boot.img"
    decompressed_file.write_bytes(b"decompressed data")

    with serve(ridesx_driver) as client:
        with patch.object(ridesx_driver, "_decompress_file", return_value=decompressed_file):
            with patch("subprocess.run") as mock_subprocess:
                mock_subprocess.side_effect = [
                    *_set_active_results(),
                    _make_ok_result("Flashing..."),
                    _make_ok_result("Continuing..."),
                ]

                client.call("flash_with_fastboot", "ABC123", {"boot": "boot.img.gz"})

                ridesx_driver._decompress_file.assert_called_once_with(compressed_file)
                flash_call = mock_subprocess.call_args_list[2]
                assert str(decompressed_file) in flash_call[0][0]


def test_flash_with_fastboot_file_not_found(temp_storage_dir, ridesx_driver):
    with serve(ridesx_driver) as client:
        from jumpstarter.client.core import DriverError

        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = [*_set_active_results()]

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
            mock_subprocess.side_effect = [
                *_set_active_results(),
                subprocess.CalledProcessError(1, "fastboot", stderr=b"flash failed"),
            ]

            with pytest.raises(DriverError, match="Failed to flash"):
                client.call("flash_with_fastboot", "ABC123", {"boot": "boot.img"})


def test_flash_with_fastboot_flash_timeout(temp_storage_dir, ridesx_driver):
    image_file = Path(temp_storage_dir) / "boot.img"
    image_file.write_bytes(b"boot image data")

    with serve(ridesx_driver) as client:
        from jumpstarter.client.core import DriverError

        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = [
                *_set_active_results(),
                subprocess.TimeoutExpired("fastboot", 20 * 60),
            ]

            with pytest.raises(DriverError, match="timeout while flashing"):
                client.call("flash_with_fastboot", "ABC123", {"boot": "boot.img"})


def test_flash_with_fastboot_continue_success(temp_storage_dir, ridesx_driver):
    image_file = Path(temp_storage_dir) / "boot.img"
    image_file.write_bytes(b"boot image data")

    with serve(ridesx_driver) as client:
        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = [
                *_set_active_results(),
                _make_ok_result("Flashing..."),
                _make_ok_result("Continuing..."),
            ]

            client.call("flash_with_fastboot", "ABC123", {"boot": "boot.img"})

            continue_call = mock_subprocess.call_args_list[3]
            assert continue_call[0][0] == ["fastboot", "-s", "ABC123", "continue"]


def test_flash_with_fastboot_continue_failure(temp_storage_dir, ridesx_driver):
    image_file = Path(temp_storage_dir) / "boot.img"
    image_file.write_bytes(b"boot image data")

    with serve(ridesx_driver) as client:
        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = [
                *_set_active_results(),
                _make_ok_result("Flashing..."),
                subprocess.CalledProcessError(1, "fastboot", stderr=b"continue failed"),
            ]

            client.call("flash_with_fastboot", "ABC123", {"boot": "boot.img"})

            assert mock_subprocess.call_count == 4


# Reset Active Slot Tests


def test_reset_active_slot(ridesx_driver):
    """_reset_active_slot runs set_active b then set_active a"""
    with patch("subprocess.run") as mock_subprocess:
        mock_subprocess.side_effect = _set_active_results()

        ridesx_driver._reset_active_slot("ABC123")

        assert mock_subprocess.call_count == 2
        assert mock_subprocess.call_args_list[0][0][0] == ["fastboot", "-s", "ABC123", "set_active", "b"]
        assert mock_subprocess.call_args_list[1][0][0] == ["fastboot", "-s", "ABC123", "set_active", "a"]


def test_reset_active_slot_failure(ridesx_driver):
    """_reset_active_slot raises on fastboot failure"""
    with patch("subprocess.run") as mock_subprocess:
        mock_subprocess.side_effect = subprocess.CalledProcessError(1, "fastboot", stderr=b"error")

        with pytest.raises(RuntimeError, match="Failed to set active slot to b"):
            ridesx_driver._reset_active_slot("ABC123")


def test_reset_active_slot_timeout(ridesx_driver):
    """_reset_active_slot raises on timeout"""
    with patch("subprocess.run") as mock_subprocess:
        mock_subprocess.side_effect = subprocess.TimeoutExpired("fastboot", 30)

        with pytest.raises(RuntimeError, match="Timeout while setting active slot to b"):
            ridesx_driver._reset_active_slot("ABC123")


def test_reset_active_slot_second_slot_failure(ridesx_driver):
    """_reset_active_slot raises if second set_active (a) fails"""
    with patch("subprocess.run") as mock_subprocess:
        mock_subprocess.side_effect = [
            _make_ok_result(),
            subprocess.CalledProcessError(1, "fastboot", stderr=b"error"),
        ]

        with pytest.raises(RuntimeError, match="Failed to set active slot to a"):
            ridesx_driver._reset_active_slot("ABC123")


# Set Active Slot Tests


def test_set_active_slot_success(ridesx_driver):
    """set_active_slot boots to fastboot, detects device, and sets active slot"""
    with serve(ridesx_driver) as client:
        with patch.object(ridesx_driver, "boot_to_fastboot", new_callable=AsyncMock) as mock_boot:
            mock_boot.return_value = {"status": "device_found", "device_id": "ABC123"}

            with patch("subprocess.run") as mock_subprocess:
                mock_subprocess.return_value = _make_ok_result()

                result = client.call("set_active_slot", "a")

                assert result["status"] == "success"
                assert result["slot"] == "a"
                assert result["device_id"] == "ABC123"
                mock_boot.assert_called_once()
                mock_subprocess.assert_called_once()
                assert mock_subprocess.call_args[0][0] == ["fastboot", "-s", "ABC123", "set_active", "a"]


def test_set_active_slot_b(ridesx_driver):
    """set_active_slot works for slot b"""
    with serve(ridesx_driver) as client:
        with patch.object(ridesx_driver, "boot_to_fastboot", new_callable=AsyncMock) as mock_boot:
            mock_boot.return_value = {"status": "device_found", "device_id": "DEV456"}

            with patch("subprocess.run") as mock_subprocess:
                mock_subprocess.return_value = _make_ok_result()

                result = client.call("set_active_slot", "b")

                assert result["status"] == "success"
                assert result["slot"] == "b"
                assert result["device_id"] == "DEV456"
                assert mock_subprocess.call_args[0][0] == ["fastboot", "-s", "DEV456", "set_active", "b"]


def test_set_active_slot_invalid(ridesx_driver):
    """set_active_slot rejects invalid slot names"""
    with serve(ridesx_driver) as client:
        with pytest.raises(ValueError, match="Invalid slot 'c'"):
            client.call("set_active_slot", "c")


def test_set_active_slot_fastboot_failure(ridesx_driver):
    """set_active_slot raises when fastboot set_active fails"""
    from jumpstarter.client.core import DriverError

    with serve(ridesx_driver) as client:
        with patch.object(ridesx_driver, "boot_to_fastboot", new_callable=AsyncMock) as mock_boot:
            mock_boot.return_value = {"status": "device_found", "device_id": "ABC123"}

            with patch("subprocess.run") as mock_subprocess:
                mock_subprocess.side_effect = subprocess.CalledProcessError(1, "fastboot", stderr=b"failed")

                with pytest.raises(DriverError, match="Failed to set active slot to a"):
                    client.call("set_active_slot", "a")


def test_set_active_slot_fastboot_timeout(ridesx_driver):
    """set_active_slot raises when fastboot times out"""
    from jumpstarter.client.core import DriverError

    with serve(ridesx_driver) as client:
        with patch.object(ridesx_driver, "boot_to_fastboot", new_callable=AsyncMock) as mock_boot:
            mock_boot.return_value = {"status": "device_found", "device_id": "ABC123"}

            with patch("subprocess.run") as mock_subprocess:
                mock_subprocess.side_effect = subprocess.TimeoutExpired("fastboot", 30)

                with pytest.raises(DriverError, match="Timeout while setting active slot to a"):
                    client.call("set_active_slot", "a")


# Boot to Fastboot Tests


@pytest.mark.asyncio
async def test_boot_to_fastboot_detects_device(ridesx_driver):
    """boot_to_fastboot calls detect_fastboot_device and returns result"""
    mock_stream = AsyncMock()
    mock_stream.receive.return_value = b"ok CMD >> "

    mock_connect = AsyncMock()
    mock_connect.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_connect.__aexit__ = AsyncMock(return_value=False)

    with patch.object(ridesx_driver.children["serial"], "connect", return_value=mock_connect):
        with patch.object(ridesx_driver, "detect_fastboot_device") as mock_detect:
            mock_detect.return_value = {"status": "device_found", "device_id": "ABC123"}

            result = await ridesx_driver.boot_to_fastboot()

            assert result["status"] == "device_found"
            assert result["device_id"] == "ABC123"
            mock_detect.assert_called_once()


@pytest.mark.asyncio
async def test_boot_to_fastboot_no_device(ridesx_driver):
    """boot_to_fastboot raises when no fastboot device is detected after boot"""
    mock_stream = AsyncMock()
    mock_stream.receive.return_value = b"ok CMD >> "

    mock_connect = AsyncMock()
    mock_connect.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_connect.__aexit__ = AsyncMock(return_value=False)

    with patch.object(ridesx_driver.children["serial"], "connect", return_value=mock_connect):
        with patch.object(ridesx_driver, "detect_fastboot_device") as mock_detect:
            mock_detect.return_value = {"status": "no_device_found", "device_id": None}

            with pytest.raises(RuntimeError, match="no fastboot device was detected"):
                await ridesx_driver.boot_to_fastboot()


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


# Flash OCI Image Tests
# Note: FLS download utilities are tested in jumpstarter.common.fls_test


def test_flash_oci_image_success(temp_storage_dir, ridesx_driver):
    with serve(ridesx_driver) as client:
        with patch("jumpstarter_driver_ridesx.driver.get_fls_binary", return_value="/usr/local/bin/fls"):
            with patch("subprocess.run") as mock_subprocess:
                mock_result = MagicMock()
                mock_result.stdout = "Flashing complete"
                mock_result.stderr = ""
                mock_result.returncode = 0
                mock_subprocess.return_value = mock_result

                result = client.call("flash_oci_image", "oci://quay.io/image:tag", None)

                assert result["status"] == "success"
                mock_subprocess.assert_called_once()
                call_args = mock_subprocess.call_args[0][0]
                assert call_args[0] == "/usr/local/bin/fls"
                assert call_args[1] == "fastboot"
                assert call_args[2] == "oci://quay.io/image:tag"


def test_flash_oci_image_with_partitions(temp_storage_dir, ridesx_driver):
    with serve(ridesx_driver) as client:
        with patch("jumpstarter_driver_ridesx.driver.get_fls_binary", return_value="fls"):
            with patch("subprocess.run") as mock_subprocess:
                mock_result = MagicMock()
                mock_result.stdout = "Flashing complete"
                mock_result.stderr = ""
                mock_result.returncode = 0
                mock_subprocess.return_value = mock_result

                partitions = {"boot_a": "boot.img", "system_a": "rootfs.simg"}
                result = client.call("flash_oci_image", "oci://image:tag", partitions)

                assert result["status"] == "success"
                call_args = mock_subprocess.call_args[0][0]
                # Check that -t flags are present for partitions
                assert "-t" in call_args
                assert "boot_a:boot.img" in call_args
                assert "system_a:rootfs.simg" in call_args


def test_flash_oci_image_error_cases(temp_storage_dir, ridesx_driver):
    """Test flash_oci_image error handling for various failure modes"""
    from jumpstarter.client.core import DriverError

    with serve(ridesx_driver) as client:
        # Reject non-oci:// schemes
        with pytest.raises(DriverError, match="OCI URL must start with oci://"):
            client.call("flash_oci_image", "docker://image:tag", None)

        with patch("jumpstarter_driver_ridesx.driver.get_fls_binary", return_value="fls"):
            with patch("subprocess.run") as mock_subprocess:
                # CalledProcessError
                error = subprocess.CalledProcessError(1, "fls")
                error.stdout = ""
                error.stderr = "Flash failed"
                mock_subprocess.side_effect = error

                with pytest.raises(DriverError, match="FLS fastboot failed: Flash failed"):
                    client.call("flash_oci_image", "oci://image:tag", None)

                # TimeoutExpired
                mock_subprocess.side_effect = subprocess.TimeoutExpired("fls", 1800)

                with pytest.raises(DriverError, match="FLS fastboot auto-detection timeout"):
                    client.call("flash_oci_image", "oci://image:tag", None)

                # FileNotFoundError
                mock_subprocess.side_effect = FileNotFoundError("fls not found")

                with pytest.raises(DriverError, match="FLS command not found"):
                    client.call("flash_oci_image", "oci://image:tag", None)


def test_flash_oci_image_with_credentials(temp_storage_dir, ridesx_driver):
    """Test that OCI credentials are passed via env vars to FLS"""
    with serve(ridesx_driver) as client:
        with patch("jumpstarter_driver_ridesx.driver.get_fls_binary", return_value="fls"):
            with patch("subprocess.run") as mock_subprocess:
                mock_result = MagicMock()
                mock_result.stdout = "Flashing complete"
                mock_result.stderr = ""
                mock_result.returncode = 0
                mock_subprocess.return_value = mock_result

                result = client.call(
                    "flash_oci_image", "oci://quay.io/private/image:tag", None, "myuser", "mypass"
                )

                assert result["status"] == "success"
                # Credentials should NOT appear in the command args
                call_args = mock_subprocess.call_args[0][0]
                assert "-u" not in call_args
                assert "-p" not in call_args
                assert "myuser" not in call_args
                assert "mypass" not in call_args
                # Credentials should be passed via env vars
                call_kwargs = mock_subprocess.call_args[1]
                env = call_kwargs["env"]
                assert env["FLS_REGISTRY_USERNAME"] == "myuser"
                assert env["FLS_REGISTRY_PASSWORD"] == "mypass"


def test_flash_oci_image_partial_credentials_rejected(temp_storage_dir, ridesx_driver):
    """Test that providing only username or only password is rejected"""
    from jumpstarter.client.core import DriverError

    with serve(ridesx_driver) as client:
        with pytest.raises(DriverError, match="OCI authentication requires both"):
            client.call("flash_oci_image", "oci://image:tag", None, "myuser", None)

        with pytest.raises(DriverError, match="OCI authentication requires both"):
            client.call("flash_oci_image", "oci://image:tag", None, None, "mypass")


def test_flash_oci_image_no_credentials(temp_storage_dir, ridesx_driver):
    """Test that omitting credentials works (anonymous access)"""
    with serve(ridesx_driver) as client:
        with patch("jumpstarter_driver_ridesx.driver.get_fls_binary", return_value="fls"):
            with patch("subprocess.run") as mock_subprocess:
                mock_result = MagicMock()
                mock_result.stdout = "Flashing complete"
                mock_result.stderr = ""
                mock_result.returncode = 0
                mock_subprocess.return_value = mock_result

                result = client.call("flash_oci_image", "oci://image:tag", None, None, None)

                assert result["status"] == "success"
                call_kwargs = mock_subprocess.call_args[1]
                env = call_kwargs["env"]
                assert "FLS_REGISTRY_USERNAME" not in env
                assert "FLS_REGISTRY_PASSWORD" not in env


def test_flash_oci_image_requires_oci_scheme(temp_storage_dir, ridesx_driver):
    """Test that only oci:// URLs are accepted"""
    from jumpstarter.client.core import DriverError

    with serve(ridesx_driver) as client:
        # Bare registry URL should be rejected
        with pytest.raises(DriverError, match="OCI URL must start with oci://"):
            client.call("flash_oci_image", "quay.io/org/image:v1", None)
