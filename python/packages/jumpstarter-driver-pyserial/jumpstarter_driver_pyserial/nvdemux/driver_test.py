import os
import tempfile
import time
from unittest.mock import MagicMock, patch

from .driver import NVDemuxSerial, _has_glob_chars, _resolve_device


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
        logger = MagicMock()
        result = _resolve_device(f.name, logger)
        assert result == f.name


def test_resolve_device_nonexistent():
    """Test device resolution with non-existent path."""
    logger = MagicMock()
    result = _resolve_device("/dev/nonexistent_device_12345", logger)
    assert result is None


def test_resolve_device_glob_pattern():
    """Test device resolution with glob pattern."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some test files
        for name in ["device_001", "device_002", "device_003"]:
            open(os.path.join(tmpdir, name), "w").close()

        logger = MagicMock()
        pattern = os.path.join(tmpdir, "device_*")
        result = _resolve_device(pattern, logger)

        # Should return first match (sorted)
        assert result == os.path.join(tmpdir, "device_001")


def test_resolve_device_glob_multiple_warns():
    """Test that multiple glob matches logs a warning."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create multiple test files
        for name in ["device_001", "device_002"]:
            open(os.path.join(tmpdir, name), "w").close()

        logger = MagicMock()
        pattern = os.path.join(tmpdir, "device_*")
        _resolve_device(pattern, logger)

        # Should have logged a warning about multiple matches
        logger.warning.assert_called_once()
        assert "Multiple devices" in logger.warning.call_args[0][0]


def test_resolve_device_glob_no_match():
    """Test device resolution with glob pattern that matches nothing."""
    logger = MagicMock()
    result = _resolve_device("/dev/nonexistent_pattern_*", logger)
    assert result is None


class MockPopen:
    """Mock subprocess.Popen for testing NVDemuxSerial."""

    def __init__(self, stdout_lines, returncode=0, delay_per_line=0.01, block_after_lines=False):
        self.stdout_lines = stdout_lines
        self.returncode = returncode
        self.delay_per_line = delay_per_line
        self.block_after_lines = block_after_lines
        self._line_index = 0
        self.stdout = self
        self.stderr = MagicMock()
        self._terminated = False

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


def test_nvdemux_parse_pts_from_line():
    """Test pts path parsing from demuxer output lines."""
    with tempfile.NamedTemporaryFile() as device_file:
        # Create a mock demuxer that outputs pts info
        stdout_lines = [
            "Starting demuxer...",
            "/dev/pts/5\tCCPLEX: 0",
            "/dev/pts/6\tBPMP: 1",
        ]

        with patch("jumpstarter_driver_pyserial.nvdemux.driver.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MockPopen(stdout_lines)

            driver = NVDemuxSerial(
                demuxer_path="/usr/bin/fake_demuxer",
                device=device_file.name,
                target="CCPLEX: 0",
                timeout=2.0,
            )

            try:
                # Wait for pts to be discovered
                assert driver._ready.wait(timeout=2.0), "Ready event not set"
                assert driver._pts_path == "/dev/pts/5"
            finally:
                driver.close()


def test_nvdemux_different_target():
    """Test selecting a different target channel."""
    with tempfile.NamedTemporaryFile() as device_file:
        stdout_lines = [
            "/dev/pts/5\tCCPLEX: 0",
            "/dev/pts/6\tBPMP: 1",
            "/dev/pts/7\tSCE: 2",
        ]

        with patch("jumpstarter_driver_pyserial.nvdemux.driver.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MockPopen(stdout_lines)

            driver = NVDemuxSerial(
                demuxer_path="/usr/bin/fake_demuxer",
                device=device_file.name,
                target="BPMP: 1",
                timeout=2.0,
            )

            try:
                assert driver._ready.wait(timeout=2.0), "Ready event not set"
                assert driver._pts_path == "/dev/pts/6"
            finally:
                driver.close()


def test_nvdemux_timeout_no_target():
    """Test timeout when target is never found."""
    with tempfile.NamedTemporaryFile() as device_file:
        # Output that doesn't contain our target, and block after to prevent restart loops
        stdout_lines = [
            "/dev/pts/5\tOTHER: 0",
            "/dev/pts/6\tANOTHER: 1",
        ]

        with patch("jumpstarter_driver_pyserial.nvdemux.driver.subprocess.Popen") as mock_popen:
            # Use block_after_lines=True to prevent the monitor thread from looping
            mock_proc = MockPopen(stdout_lines, block_after_lines=True)
            mock_popen.return_value = mock_proc

            driver = NVDemuxSerial(
                demuxer_path="/usr/bin/fake_demuxer",
                device=device_file.name,
                target="CCPLEX: 0",
                timeout=0.5,
            )

            try:
                # Should timeout since target is never found
                assert not driver._ready.is_set()
                assert driver._pts_path is None
            finally:
                driver.close()


def test_nvdemux_demuxer_args():
    """Test that demuxer is called with correct arguments."""
    with tempfile.NamedTemporaryFile() as device_file:
        stdout_lines = ["/dev/pts/5\tCCPLEX: 0"]

        with patch("jumpstarter_driver_pyserial.nvdemux.driver.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MockPopen(stdout_lines)

            driver = NVDemuxSerial(
                demuxer_path="/opt/nvidia/demuxer",
                device=device_file.name,
                chip="T234",
                target="CCPLEX: 0",
                timeout=2.0,
            )

            try:
                driver._ready.wait(timeout=2.0)

                # Check that Popen was called with correct args
                mock_popen.assert_called()
                call_args = mock_popen.call_args
                cmd = call_args[0][0]

                assert cmd[0] == "/opt/nvidia/demuxer"
                assert "-m" in cmd
                assert cmd[cmd.index("-m") + 1] == "T234"
                assert "-d" in cmd
                assert cmd[cmd.index("-d") + 1] == device_file.name
            finally:
                driver.close()


def test_nvdemux_default_values():
    """Test default parameter values."""
    with tempfile.NamedTemporaryFile() as device_file:
        stdout_lines = ["/dev/pts/5\tCCPLEX: 0"]

        with patch("jumpstarter_driver_pyserial.nvdemux.driver.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MockPopen(stdout_lines)

            driver = NVDemuxSerial(
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                timeout=2.0,
            )

            try:
                # Check defaults
                assert driver.chip == "T264"
                assert driver.target == "CCPLEX: 0"
                assert driver.baudrate == 115200
                assert driver.poll_interval == 1.0
            finally:
                driver.close()


def test_nvdemux_close_terminates_process():
    """Test that close() terminates the demuxer process."""
    with tempfile.NamedTemporaryFile() as device_file:
        # Long-running output simulation
        stdout_lines = ["/dev/pts/5\tCCPLEX: 0"] + ["keep alive"] * 100

        with patch("jumpstarter_driver_pyserial.nvdemux.driver.subprocess.Popen") as mock_popen:
            mock_proc = MockPopen(stdout_lines, delay_per_line=0.1)
            mock_popen.return_value = mock_proc

            driver = NVDemuxSerial(
                demuxer_path="/usr/bin/demuxer",
                device=device_file.name,
                timeout=2.0,
            )

            # Wait for ready
            driver._ready.wait(timeout=2.0)

            # Close should terminate the process
            driver.close()

            assert mock_proc._terminated


def test_nvdemux_client_class():
    """Test that NVDemuxSerial uses PySerialClient."""
    assert NVDemuxSerial.client() == "jumpstarter_driver_pyserial.client.PySerialClient"
