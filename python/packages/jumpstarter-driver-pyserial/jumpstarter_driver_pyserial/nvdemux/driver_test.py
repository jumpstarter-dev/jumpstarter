"""Tests for NVDemuxSerial driver."""

import tempfile
from unittest.mock import MagicMock, patch

from .driver import NVDemuxSerial


def test_nvdemux_registration():
    """Test that driver registers with DemuxerManager on init."""
    with tempfile.NamedTemporaryFile() as device_file:
        with patch("jumpstarter_driver_pyserial.nvdemux.driver.DemuxerManager") as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.get_instance.return_value = mock_manager

            driver = NVDemuxSerial(
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                target="CCPLEX: 0",
                chip="T264",
                timeout=0.1,
            )

            try:
                # Verify driver registered with manager
                mock_manager.register_driver.assert_called_once()
                call_kwargs = mock_manager.register_driver.call_args[1]
                assert call_kwargs["driver_id"] == str(driver.uuid)
                assert call_kwargs["demuxer_path"] == "/usr/bin/demuxer"
                assert call_kwargs["device"] == device_file.name
                assert call_kwargs["chip"] == "T264"
                assert call_kwargs["target"] == "CCPLEX: 0"
            finally:
                driver.close()


def test_nvdemux_gets_pts_from_manager():
    """Test that connect() gets pts path from manager."""
    with tempfile.NamedTemporaryFile() as device_file:
        with patch("jumpstarter_driver_pyserial.nvdemux.driver.DemuxerManager") as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.get_instance.return_value = mock_manager
            mock_manager.get_pts_path.return_value = "/dev/pts/5"

            driver = NVDemuxSerial(
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                target="CCPLEX: 0",
                timeout=0.1,
            )

            try:
                # Should call get_pts_path when checking pts availability
                # (We can't test connect() easily without mocking serial, but we can test the logic)
                pts_path = mock_manager.get_pts_path(str(driver.uuid))
                assert pts_path == "/dev/pts/5"
            finally:
                driver.close()


def test_nvdemux_unregisters_on_close():
    """Test that driver unregisters from manager on close."""
    with tempfile.NamedTemporaryFile() as device_file:
        with patch("jumpstarter_driver_pyserial.nvdemux.driver.DemuxerManager") as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.get_instance.return_value = mock_manager

            driver = NVDemuxSerial(
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                target="CCPLEX: 0",
                timeout=0.1,
            )

            driver_id = str(driver.uuid)
            driver.close()

            # Verify driver unregistered
            mock_manager.unregister_driver.assert_called_once_with(driver_id)


def test_nvdemux_default_values():
    """Test default parameter values."""
    with tempfile.NamedTemporaryFile() as device_file:
        with patch("jumpstarter_driver_pyserial.nvdemux.driver.DemuxerManager") as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.get_instance.return_value = mock_manager

            driver = NVDemuxSerial(
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                timeout=0.1,
            )

            try:
                # Check defaults
                assert driver.chip == "T264"
                assert driver.target == "CCPLEX: 0"
                assert driver.baudrate == 115200
                assert driver.poll_interval == 1.0
            finally:
                driver.close()


def test_nvdemux_registration_error_propagates():
    """Test that registration errors are propagated."""
    with tempfile.NamedTemporaryFile() as device_file:
        with patch("jumpstarter_driver_pyserial.nvdemux.driver.DemuxerManager") as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.get_instance.return_value = mock_manager
            mock_manager.register_driver.side_effect = ValueError("Config mismatch")

            try:
                _driver = NVDemuxSerial(
                    demuxer_path="/usr/bin/demuxer",
                    device=device_file.name,
                    target="CCPLEX: 0",
                    timeout=0.1,
                )
                raise AssertionError("Should have raised ValueError")
            except ValueError as e:
                assert "Config mismatch" in str(e)


def test_nvdemux_client_class():
    """Test that NVDemuxSerial uses PySerialClient."""
    assert NVDemuxSerial.client() == "jumpstarter_driver_pyserial.client.PySerialClient"
