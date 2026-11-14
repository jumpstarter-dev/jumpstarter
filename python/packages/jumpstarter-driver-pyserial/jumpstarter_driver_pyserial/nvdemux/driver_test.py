"""Tests for NVDemuxSerial driver."""

import tempfile
from unittest.mock import MagicMock, patch

import pytest
from anyio import BrokenResourceError, create_memory_object_stream, fail_after

from ..driver_test import _console_client, _wait_for_console_status
from .driver import NVDemuxSerial
from jumpstarter.common.utils import serve


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


def test_nvdemux_streams_share_source_until_session_shutdown():
    """Observers and consoles share one pts across attachments; session teardown closes it."""
    tx, rx = create_memory_object_stream[bytes](1)

    async def read(_max_bytes):
        return await rx.receive()

    writer = MagicMock()

    with (
        tx,
        rx,
        patch("jumpstarter_driver_pyserial.nvdemux.driver.DemuxerManager") as mock_manager_class,
        patch(
            "jumpstarter_driver_pyserial.nvdemux.driver.open_serial_connection",
            return_value=(MagicMock(read=read), writer),
        ) as open_serial,
    ):
        manager = mock_manager_class.get_instance.return_value
        manager.get_pts_path.side_effect = [None, "/dev/pts/5"]

        async def scenario(client):
            with fail_after(5):
                for data in (b"CCPLEX boot\n", b"CCPLEX ready\n"):
                    async with _console_client(client) as console, _console_client(client, "observe") as observer:
                        await tx.send(data)
                        assert await console.receive() == data
                        assert await observer.receive() == data
                    await _wait_for_console_status(client, total_clients=0)
                    writer.close.assert_not_called()

                open_serial.assert_awaited_once_with(url="/dev/pts/5", baudrate=115200)

        with serve(NVDemuxSerial(demuxer_path="/usr/bin/demuxer")) as client:
            client.portal.call(scenario, client)

        writer.close.assert_called_once_with()


def test_nvdemux_attach_fails_at_configured_timeout():
    """A missing pts fails the attach at the configured timeout, not fan-out's 30s default."""
    with patch("jumpstarter_driver_pyserial.nvdemux.driver.DemuxerManager") as mock_manager_class:
        mock_manager_class.get_instance.return_value.get_pts_path.return_value = None

        async def scenario(client):
            with fail_after(5), pytest.raises(BrokenResourceError) as exc_info:
                async with client.stream_async("connect") as console:
                    await console.receive()
            failure = str(exc_info.value.__cause__)
            assert "TimeoutError" in failure
            assert "source not ready within 0.2s" in failure

        with serve(NVDemuxSerial(demuxer_path="/usr/bin/demuxer", timeout=0.2)) as client:
            client.portal.call(scenario, client)
