"""Tests for DemuxerManager."""

import os
import tempfile
import time
from unittest.mock import patch

from .manager import DemuxerManager, _has_glob_chars, _resolve_device


def test_has_glob_chars():
    """Test glob character detection."""
    assert _has_glob_chars("/dev/ttyUSB*") is True
    assert _has_glob_chars("/dev/serial/by-id/usb-NVIDIA_*-if01") is True
    assert _has_glob_chars("/dev/tty[0-9]") is True
    assert _has_glob_chars("/dev/ttyUSB?") is True
    assert _has_glob_chars("/dev/ttyUSB0") is False
    assert _has_glob_chars("/dev/serial/by-id/usb-NVIDIA_ABC123-if01") is False


def test_resolve_device_direct_path():
    """Test device resolution with direct path."""
    with tempfile.NamedTemporaryFile() as f:
        result = _resolve_device(f.name)
        assert result == f.name


def test_resolve_device_nonexistent():
    """Test device resolution with non-existent path."""
    result = _resolve_device("/dev/nonexistent_device_12345")
    assert result is None


def test_resolve_device_glob_pattern():
    """Test device resolution with glob pattern."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some test files
        for name in ["device_001", "device_002", "device_003"]:
            open(os.path.join(tmpdir, name), "w").close()

        pattern = os.path.join(tmpdir, "device_*")
        result = _resolve_device(pattern)

        # Should return first match (sorted)
        assert result == os.path.join(tmpdir, "device_001")


def test_resolve_device_glob_no_match():
    """Test device resolution with glob pattern that matches nothing."""
    result = _resolve_device("/dev/nonexistent_pattern_*")
    assert result is None


class MockStderr:
    """Mock stderr file-like object for testing."""

    def __init__(self, stderr_lines=None, terminated_callback=None):
        self.stderr_lines = stderr_lines or []
        self._line_index = 0
        self._terminated_callback = terminated_callback

    def readline(self):
        if self._terminated_callback and self._terminated_callback():
            return ""
        if self._line_index >= len(self.stderr_lines):
            return ""
        line = self.stderr_lines[self._line_index]
        self._line_index += 1
        return line + "\n"


class MockPopen:
    """Mock subprocess.Popen for testing."""

    _next_pid = 1000

    def __init__(self, stdout_lines, returncode=0, delay_per_line=0.01, block_after_lines=False, stderr_lines=None):
        self.stdout_lines = stdout_lines
        self.returncode = returncode
        self.delay_per_line = delay_per_line
        self.block_after_lines = block_after_lines
        self._line_index = 0
        self.stdout = self
        self.stderr = MockStderr(stderr_lines=stderr_lines, terminated_callback=lambda: self._terminated)
        self._terminated = False
        # Assign a mock PID
        self.pid = MockPopen._next_pid
        MockPopen._next_pid += 1

    def readline(self):
        if self._terminated:
            return ""
        if self._line_index >= len(self.stdout_lines):
            if self.block_after_lines:
                # Block until terminated (simulates long-running process)
                while not self._terminated:
                    time.sleep(0.01)
            return ""
        time.sleep(self.delay_per_line)
        line = self.stdout_lines[self._line_index]
        self._line_index += 1
        return line + "\n"

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self._terminated = True

    def kill(self):
        self._terminated = True


def test_manager_singleton():
    """Test that DemuxerManager is a singleton."""
    # Reset singleton for testing
    DemuxerManager.reset_instance()

    manager1 = DemuxerManager.get_instance()
    manager2 = DemuxerManager.get_instance()

    assert manager1 is manager2

    # Cleanup
    DemuxerManager.reset_instance()


def test_single_driver_registration():
    """Test registering a single driver."""
    DemuxerManager.reset_instance()

    with tempfile.NamedTemporaryFile() as device_file:
        stdout_lines = [
            "/dev/pts/5\tCCPLEX: 0",
            "/dev/pts/6\tBPMP: 1",
        ]

        with patch("jumpstarter_driver_pyserial.nvdemux.manager.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MockPopen(stdout_lines, block_after_lines=True)

            manager = DemuxerManager.get_instance()

            manager.register_driver(
                driver_id="driver1",
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                chip="T264",
                target="CCPLEX: 0",
            )

            # Wait for demuxer to process output
            time.sleep(0.5)

            # Verify pts path is available
            pts_path = manager.get_pts_path("driver1")
            assert pts_path == "/dev/pts/5"

            # Cleanup
            manager.unregister_driver("driver1")
            DemuxerManager.reset_instance()


def test_multiple_drivers_single_process():
    """Test that multiple drivers share a single demuxer process."""
    DemuxerManager.reset_instance()

    with tempfile.NamedTemporaryFile() as device_file:
        stdout_lines = [
            "/dev/pts/5\tCCPLEX: 0",
            "/dev/pts/6\tBPMP: 1",
            "/dev/pts/7\tSCE: 2",
        ]

        with patch("jumpstarter_driver_pyserial.nvdemux.manager.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MockPopen(stdout_lines, block_after_lines=True)

            manager = DemuxerManager.get_instance()

            # Register three drivers
            manager.register_driver(
                driver_id="driver1",
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                chip="T264",
                target="CCPLEX: 0",
            )

            manager.register_driver(
                driver_id="driver2",
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                chip="T264",
                target="BPMP: 1",
            )

            manager.register_driver(
                driver_id="driver3",
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                chip="T264",
                target="SCE: 2",
            )

            # Wait for demuxer to process output
            time.sleep(0.5)

            # Verify process was only started once
            assert mock_popen.call_count == 1

            # Verify all pts paths are available
            assert manager.get_pts_path("driver1") == "/dev/pts/5"
            assert manager.get_pts_path("driver2") == "/dev/pts/6"
            assert manager.get_pts_path("driver3") == "/dev/pts/7"

            # Cleanup
            manager.unregister_driver("driver1")
            manager.unregister_driver("driver2")
            manager.unregister_driver("driver3")
            DemuxerManager.reset_instance()


def test_config_validation_demuxer_path_mismatch():
    """Test that mismatched demuxer_path raises error."""
    DemuxerManager.reset_instance()

    with tempfile.NamedTemporaryFile() as device_file:
        stdout_lines = ["/dev/pts/5\tCCPLEX: 0"]

        with patch("jumpstarter_driver_pyserial.nvdemux.manager.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MockPopen(stdout_lines, block_after_lines=True)

            manager = DemuxerManager.get_instance()

            # Register first driver
            manager.register_driver(
                driver_id="driver1",
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                chip="T264",
                target="CCPLEX: 0",
            )

            # Try to register second driver with different demuxer_path
            try:
                manager.register_driver(
                    driver_id="driver2",
                    demuxer_path="/opt/nvidia/demuxer",  # Different path
                    device=device_file.name,
                    chip="T264",
                    target="BPMP: 1",
                )
                raise AssertionError("Should have raised ValueError")
            except ValueError as e:
                assert "Demuxer path mismatch" in str(e)

            # Cleanup
            manager.unregister_driver("driver1")
            DemuxerManager.reset_instance()


def test_config_validation_device_mismatch():
    """Test that mismatched device raises error."""
    DemuxerManager.reset_instance()

    with tempfile.NamedTemporaryFile() as device_file1, tempfile.NamedTemporaryFile() as device_file2:
        stdout_lines = ["/dev/pts/5\tCCPLEX: 0"]

        with patch("jumpstarter_driver_pyserial.nvdemux.manager.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MockPopen(stdout_lines, block_after_lines=True)

            manager = DemuxerManager.get_instance()

            # Register first driver
            manager.register_driver(
                driver_id="driver1",
                demuxer_path="/usr/bin/demuxer",
                device=device_file1.name,
                chip="T264",
                target="CCPLEX: 0",
            )

            # Try to register second driver with different device
            try:
                manager.register_driver(
                    driver_id="driver2",
                    demuxer_path="/usr/bin/demuxer",
                    device=device_file2.name,  # Different device
                    chip="T264",
                    target="BPMP: 1",
                )
                raise AssertionError("Should have raised ValueError")
            except ValueError as e:
                assert "Device mismatch" in str(e)

            # Cleanup
            manager.unregister_driver("driver1")
            DemuxerManager.reset_instance()


def test_config_validation_chip_mismatch():
    """Test that mismatched chip raises error."""
    DemuxerManager.reset_instance()

    with tempfile.NamedTemporaryFile() as device_file:
        stdout_lines = ["/dev/pts/5\tCCPLEX: 0"]

        with patch("jumpstarter_driver_pyserial.nvdemux.manager.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MockPopen(stdout_lines, block_after_lines=True)

            manager = DemuxerManager.get_instance()

            # Register first driver
            manager.register_driver(
                driver_id="driver1",
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                chip="T264",
                target="CCPLEX: 0",
            )

            # Try to register second driver with different chip
            try:
                manager.register_driver(
                    driver_id="driver2",
                    demuxer_path="/usr/bin/demuxer",
                    device=device_file.name,
                    chip="T234",  # Different chip
                    target="BPMP: 1",
                )
                raise AssertionError("Should have raised ValueError")
            except ValueError as e:
                assert "Chip mismatch" in str(e)

            # Cleanup
            manager.unregister_driver("driver1")
            DemuxerManager.reset_instance()


def test_duplicate_target_rejected():
    """Test that duplicate target registration is rejected."""
    DemuxerManager.reset_instance()

    with tempfile.NamedTemporaryFile() as device_file:
        stdout_lines = ["/dev/pts/5\tCCPLEX: 0"]

        with patch("jumpstarter_driver_pyserial.nvdemux.manager.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MockPopen(stdout_lines, block_after_lines=True)

            manager = DemuxerManager.get_instance()

            # Register first driver
            manager.register_driver(
                driver_id="driver1",
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                chip="T264",
                target="CCPLEX: 0",
            )

            # Try to register second driver with same target
            try:
                manager.register_driver(
                    driver_id="driver2",
                    demuxer_path="/usr/bin/demuxer",
                    device=device_file.name,
                    chip="T264",
                    target="CCPLEX: 0",  # Same target
                )
                raise AssertionError("Should have raised ValueError")
            except ValueError as e:
                assert "already registered" in str(e)

            # Cleanup
            manager.unregister_driver("driver1")
            DemuxerManager.reset_instance()


def test_reference_counting():
    """Test that process starts/stops based on driver registration."""
    DemuxerManager.reset_instance()

    with tempfile.NamedTemporaryFile() as device_file:
        stdout_lines = [
            "/dev/pts/5\tCCPLEX: 0",
            "/dev/pts/6\tBPMP: 1",
        ]

        with patch("jumpstarter_driver_pyserial.nvdemux.manager.subprocess.Popen") as mock_popen:
            mock_proc = MockPopen(stdout_lines, block_after_lines=True)
            mock_popen.return_value = mock_proc

            manager = DemuxerManager.get_instance()

            # Initially no process should be running
            assert manager._process is None

            # Register first driver - process should start
            manager.register_driver(
                driver_id="driver1",
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                chip="T264",
                target="CCPLEX: 0",
            )

            time.sleep(0.2)
            assert mock_popen.call_count == 1

            # Register second driver - process should NOT restart
            manager.register_driver(
                driver_id="driver2",
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                chip="T264",
                target="BPMP: 1",
            )

            time.sleep(0.2)
            assert mock_popen.call_count == 1  # Still just one call

            # Unregister first driver - process should continue
            manager.unregister_driver("driver1")
            time.sleep(0.2)
            assert not mock_proc._terminated

            # Unregister second driver - process should still continue (monitor stays running)
            manager.unregister_driver("driver2")
            time.sleep(0.2)
            assert not mock_proc._terminated

            # Cleanup
            DemuxerManager.reset_instance()


def test_pts_path_available_for_ready_target():
    """Test that pts path is available for already-ready targets."""
    DemuxerManager.reset_instance()

    with tempfile.NamedTemporaryFile() as device_file:
        stdout_lines = [
            "/dev/pts/5\tCCPLEX: 0",
            "/dev/pts/6\tBPMP: 1",
        ]

        with patch("jumpstarter_driver_pyserial.nvdemux.manager.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MockPopen(stdout_lines, block_after_lines=True)

            manager = DemuxerManager.get_instance()

            # Register first driver
            manager.register_driver(
                driver_id="driver1",
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                chip="T264",
                target="CCPLEX: 0",
            )

            time.sleep(0.5)
            assert manager.get_pts_path("driver1") == "/dev/pts/5"

            # Register second driver for already-ready target
            manager.register_driver(
                driver_id="driver2",
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                chip="T264",
                target="BPMP: 1",
            )

            # pts path should be immediately available
            assert manager.get_pts_path("driver2") == "/dev/pts/6"

            # Cleanup
            manager.unregister_driver("driver1")
            manager.unregister_driver("driver2")
            DemuxerManager.reset_instance()
