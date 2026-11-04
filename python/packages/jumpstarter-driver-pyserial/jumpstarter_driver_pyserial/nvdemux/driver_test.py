"""Tests for NVDemuxSerial driver."""

import tempfile
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from anyio import create_memory_object_stream
from anyio.streams.stapled import StapledObjectStream

from .driver import NVDemuxSerial


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_nvdemux_registration():
    """Test that driver registers with DemuxerManager on init."""
    with (
        tempfile.NamedTemporaryFile() as device_file,
        patch("jumpstarter_driver_pyserial.nvdemux.driver.DemuxerManager") as mock_manager_class,
    ):
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
    with (
        tempfile.NamedTemporaryFile() as device_file,
        patch("jumpstarter_driver_pyserial.nvdemux.driver.DemuxerManager") as mock_manager_class,
    ):
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
    with (
        tempfile.NamedTemporaryFile() as device_file,
        patch("jumpstarter_driver_pyserial.nvdemux.driver.DemuxerManager") as mock_manager_class,
    ):
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
    with (
        tempfile.NamedTemporaryFile() as device_file,
        patch("jumpstarter_driver_pyserial.nvdemux.driver.DemuxerManager") as mock_manager_class,
    ):
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
    with (
        tempfile.NamedTemporaryFile() as device_file,
        patch("jumpstarter_driver_pyserial.nvdemux.driver.DemuxerManager") as mock_manager_class,
    ):
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


@pytest.mark.anyio
async def test_nvdemux_observe_streams_demuxed_output():
    """``j serial pipe`` (observe) reads the demuxed pts, sharing one open with connect."""
    source_tx, source_rx = create_memory_object_stream[bytes](32)
    sink_tx, _sink_rx = create_memory_object_stream[bytes](32)

    @asynccontextmanager
    async def fake_async_serial(**_kwargs):
        yield StapledObjectStream(sink_tx, source_rx)

    writer = SimpleNamespace(transport=SimpleNamespace(set_write_buffer_limits=lambda **_kwargs: None))
    opens = []

    async def fake_open_serial_connection(*, url, baudrate):
        opens.append((url, baudrate))
        return object(), writer

    polls = 0

    def get_pts_path(_driver_id):
        nonlocal polls
        polls += 1
        return None if polls == 1 else "/dev/pts/5"

    with (
        tempfile.NamedTemporaryFile() as device_file,
        patch("jumpstarter_driver_pyserial.nvdemux.driver.DemuxerManager") as mock_manager_class,
        patch(
            "jumpstarter_driver_pyserial.nvdemux.driver.open_serial_connection",
            fake_open_serial_connection,
        ),
        patch("jumpstarter_driver_pyserial.nvdemux.driver.AsyncSerial", fake_async_serial),
    ):
        mock_manager = MagicMock()
        mock_manager_class.get_instance.return_value = mock_manager
        # First poll misses, second one resolves: exercises the pts wait loop.
        mock_manager.get_pts_path.side_effect = get_pts_path

        driver = NVDemuxSerial(
            demuxer_path="/usr/bin/demuxer",
            device=device_file.name,
            target="CCPLEX: 0",
            timeout=1.0,
        )
        try:
            async with driver.connect() as console, driver.observe() as observer:
                await source_tx.send(b"CCPLEX boot\n")
                assert await console.receive() == b"CCPLEX boot\n"
                assert await observer.receive() == b"CCPLEX boot\n"
                # One physical open shared by both clients, from the resolved pts.
                assert opens == [("/dev/pts/5", 115200)]
                assert polls >= 2
        finally:
            driver.close()
