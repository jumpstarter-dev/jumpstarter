import socket
import threading
from unittest.mock import AsyncMock, patch

import anyio
import pytest
from anyio import TASK_STATUS_IGNORED, CancelScope, to_thread

from jumpstarter_driver_tftp import driver as driver_module
from jumpstarter_driver_tftp import server as server_module
from jumpstarter_driver_tftp.driver import Tftp, TftpError
from jumpstarter_driver_tftp.server import Opcode, TftpServer

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


def test_tftp_advertised_host(tmp_path):
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", advertised_host="192.168.100.1")
    assert server.get_host() == "192.168.100.1"
    # without advertised_host, get_host returns the bind host
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1")
    assert server.get_host() == "127.0.0.1"


def test_tftp_root_directory_creation(tmp_path):
    new_dir = tmp_path / "new_tftp_root"
    server = Tftp(root_dir=str(new_dir))
    assert new_dir.exists()
    server.close()


def test_tftp_start_stop(tmp_path):
    """Test that start/stop lifecycle works via anyio.run() in the server thread."""
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=0)
    server.start()
    try:
        # _run_server_lifecycle ran: the server was created
        assert server.server is not None
    finally:
        server.stop()
    server.close()


def test_tftp_start_stop_ends_the_server_thread(tmp_path):
    """Test that the server thread is gone after shutdown."""
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=0)
    server.start()
    server.stop()
    assert server.server_thread is None
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
    """Test that _run_server_lifecycle creates TftpServer within the event loop."""
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=0)

    # Patch _run_server so we don't actually start listening
    with patch.object(server, "_run_server", new_callable=AsyncMock):
        await server._run_server_lifecycle()

    # Server was created in async context
    assert server.server is not None
    assert server.server.shutdown_event is not None
    assert server.server.ready_event is not None
    server.close()


def test_tftp_start_surfaces_startup_error(tmp_path):
    """Test that start() surfaces the real error instead of a generic timeout.

    If TftpServer construction fails inside _run_server_lifecycle, the
    _startup_error field is set and _loop_ready is signalled via finally,
    so start() returns promptly and raises the actual cause.
    """
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=0)

    with patch(
        "jumpstarter_driver_tftp.driver.TftpServer",
        side_effect=RuntimeError("port already in use"),
    ), pytest.raises(TftpError, match="port already in use"):
        server.start()

    server.close()


@pytest.fixture
def free_ipv4_udp_port(free_udp_port_factory):
    return free_udp_port_factory(socket.AF_INET)


def test_tftp_stop_releases_the_port(tmp_path, free_ipv4_udp_port):
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=free_ipv4_udp_port)
    server.start()
    server.stop()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", free_ipv4_udp_port))
    server.close()


def _download(port: int, filename: str) -> bytes:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(5)
        sock.sendto(Opcode.RRQ.to_bytes(2, "big") + f"{filename}\0octet\0".encode(), ("127.0.0.1", port))
        data = b""
        while True:
            packet, transfer = sock.recvfrom(65536)
            assert int.from_bytes(packet[:2], "big") == Opcode.DATA
            data += packet[4:]
            sock.sendto(Opcode.ACK.to_bytes(2, "big") + packet[2:4], transfer)
            if len(packet) - 4 < 512:
                return data


def test_tftp_start_returns_once_the_server_listens(tmp_path, free_ipv4_udp_port, monkeypatch):
    create_listener = server_module.create_udp_socket

    async def slow_to_bind(**kwargs):
        await anyio.sleep(0.2)
        return await create_listener(**kwargs)

    monkeypatch.setattr(server_module, "create_udp_socket", slow_to_bind)
    (tmp_path / "hello.txt").write_bytes(b"hello over tftp")
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=free_ipv4_udp_port)

    server.start()
    try:
        assert _download(free_ipv4_udp_port, "hello.txt") == b"hello over tftp"
    finally:
        server.stop()
    server.close()


def test_tftp_start_raises_when_the_port_is_taken(tmp_path, free_ipv4_udp_port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as taken:
        taken.bind(("127.0.0.1", free_ipv4_udp_port))
        server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=free_ipv4_udp_port)

        with pytest.raises(TftpError, match="Failed to start TFTP server: .*Address already in use"):
            server.start()
    server.close()


class _Call(threading.Thread):
    """Runs *call* in a thread of its own, keeping what it raised."""

    def __init__(self, call):
        super().__init__(daemon=True)
        self._call = call
        self.error: Exception | None = None

    def run(self):
        try:
            self._call()
        except Exception as e:  # noqa: BLE001 - the test inspects whatever the thread raised
            self.error = e


@pytest.fixture
def held_stop(monkeypatch):
    """Make the driver's server hold its stop, after closing its socket, until released."""
    stopping, release = threading.Event(), threading.Event()

    class HeldStop(TftpServer):
        async def start(self, *, task_status=TASK_STATUS_IGNORED):
            try:
                await super().start(task_status=task_status)
            finally:
                stopping.set()
                with CancelScope(shield=True):
                    await to_thread.run_sync(release.wait)

    monkeypatch.setattr(driver_module, "TftpServer", HeldStop)
    yield stopping, release
    release.set()


def _returns_while(release: threading.Event, held: _Call, overlapping: _Call) -> bool:
    """Whether *overlapping* returns while *held* is still stopping the server; neither may raise."""
    overlapping.start()
    overlapping.join(0.2)
    returned = not overlapping.is_alive()
    release.set()
    held.join(5)
    overlapping.join(5)
    assert not held.is_alive() and not overlapping.is_alive()
    assert held.error is None and overlapping.error is None
    return returned


def test_a_stop_overlapping_another_returns_once_the_server_stopped(tmp_path, free_ipv4_udp_port, held_stop):
    stopping, release = held_stop
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=free_ipv4_udp_port)
    server.start()
    first = _Call(server.stop)
    first.start()
    assert stopping.wait(5)

    assert not _returns_while(release, first, _Call(server.stop))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", free_ipv4_udp_port))
    server.close()


def test_a_start_overlapping_a_stop_starts_once_the_stop_finished(tmp_path, free_ipv4_udp_port, held_stop):
    stopping, release = held_stop
    (tmp_path / "hello.txt").write_bytes(b"hello again")
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=free_ipv4_udp_port)
    server.start()
    stopper = _Call(server.stop)
    stopper.start()
    assert stopping.wait(5)

    try:
        assert not _returns_while(release, stopper, _Call(server.start))
        assert _download(free_ipv4_udp_port, "hello.txt") == b"hello again"
    finally:
        server.stop()
    server.close()
