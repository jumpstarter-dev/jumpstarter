import socket

import pytest

from jumpstarter_driver_tftp.driver import Tftp, TftpError
from jumpstarter_driver_tftp.server import Opcode

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


def test_tftp_serves_files_between_start_and_stop(tmp_path, free_udp_port):
    (tmp_path / "hello.txt").write_bytes(b"hello over tftp")
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=free_udp_port)

    server.start()
    try:
        assert _download(free_udp_port, "hello.txt") == b"hello over tftp"
    finally:
        server.stop()
    server.close()


def test_tftp_stop_releases_the_port(tmp_path, free_udp_port):
    server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=free_udp_port)
    server.start()
    server.stop()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", free_udp_port))
    server.close()


def test_tftp_start_raises_when_the_port_is_taken(tmp_path, free_udp_port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as taken:
        taken.bind(("127.0.0.1", free_udp_port))
        server = Tftp(root_dir=str(tmp_path), host="127.0.0.1", port=free_udp_port)

        with pytest.raises(TftpError, match="Failed to start TFTP server"):
            server.start()
    server.close()
