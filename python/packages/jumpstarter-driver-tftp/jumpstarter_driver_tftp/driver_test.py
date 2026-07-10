import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from jumpstarter_driver_tftp.driver import Tftp

from jumpstarter.common.utils import serve


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def tftp(tmp_path):
    with serve(Tftp(root_dir=str(tmp_path), host="127.0.0.1")) as client:
        try:
            yield client
        finally:
            client.close()


@pytest.mark.anyio
async def test_tftp_file_operations(tftp, tmp_path):
    filename = "test.txt"
    test_data = b"Hello"

    tftp.storage.write_bytes(filename, test_data)

    files = list(tftp.storage.list("/"))
    assert filename in files

    tftp.storage.delete(filename)
    assert filename not in list(tftp.storage.list("/"))


def test_tftp_host_config(tmp_path):
    custom_host = "192.168.1.1"
    server = Tftp(root_dir=str(tmp_path), host=custom_host)
    assert server.get_host() == custom_host


def test_tftp_root_directory_creation(tmp_path):
    new_dir = tmp_path / "new_tftp_root"
    server = Tftp(root_dir=str(new_dir))
    assert new_dir.exists()
    server.close()


def test_tftp_start_stop(tmp_path):
    """Test that start/stop lifecycle works via asyncio.run() in the server thread."""
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=0)
    server.start()
    try:
        # _run_server_lifecycle ran: loop was captured and server was created
        assert server.server is not None
        assert server._loop is not None
    finally:
        server.stop()
    server.close()


def test_tftp_start_stop_cleans_up_loop(tmp_path):
    """Test that _loop is set to None after shutdown."""
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=0)
    server.start()
    server.stop()
    # After stop, the thread has exited and _loop should be cleaned up
    assert server._loop is None
    server.close()


def test_tftp_start_server_logs_error_on_failure(tmp_path):
    """Test the error handling path in _start_server."""
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=0)

    with patch.object(server, "_run_server_lifecycle", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        # _start_server runs in the calling thread here (not via start())
        server._start_server()

    # Should not raise, error is logged
    server.close()


@pytest.mark.anyio
async def test_tftp_run_server_lifecycle_creates_server_in_async_context(tmp_path):
    """Test that _run_server_lifecycle creates TftpServer with a running event loop.

    This is the key fix: asyncio.Event() objects inside TftpServer are now
    created with a running loop, which is required by Python 3.14+.
    """
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=0)

    # Patch _run_server so we don't actually start listening
    with patch.object(server, "_run_server", new_callable=AsyncMock):
        await server._run_server_lifecycle()

    # Server was created in async context, Event() objects are bound to a loop
    assert server.server is not None
    assert server.server.shutdown_event is not None
    assert server.server.ready_event is not None
    # Loop ref is cleaned up in the finally block
    assert server._loop is None
    server.close()
