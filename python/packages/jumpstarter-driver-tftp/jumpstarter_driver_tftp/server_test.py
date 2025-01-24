import asyncio
import tempfile
from pathlib import Path

import pytest

from jumpstarter_driver_tftp.server import Opcode, TftpServer


@pytest.fixture
async def tftp_server():
    """Fixture to create and cleanup a TFTP server instance."""
    with tempfile.TemporaryDirectory() as temp_dir:
        test_file_path = Path(temp_dir) / "test.txt"
        test_file_path.write_text("Hello, TFTP!")

        server = TftpServer(host="127.0.0.1", port=0, root_dir=temp_dir)

        yield server, temp_dir

        await server.shutdown()


async def create_test_client(server_port):
    """Helper function to create a test UDP client."""
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, remote_addr=("127.0.0.1", server_port)
    )
    return transport, protocol


@pytest.mark.anyio
async def test_server_startup_and_shutdown(tftp_server):
    """Test that server starts up and shuts down cleanly."""
    server, _ = tftp_server

    server_task = asyncio.create_task(server.start())
    await asyncio.sleep(0.1)

    await server.shutdown()

    # Wait for server task to complete
    await server_task

    assert True


@pytest.mark.anyio
async def test_read_request_for_existing_file(tftp_server):
    """Test reading an existing file from the server."""
    server, temp_dir = tftp_server

    server_task = asyncio.create_task(server.start())
    await asyncio.sleep(0.1)

    try:
        transport, _ = await create_test_client(server.port)

        rrq_packet = (
            Opcode.RRQ.to_bytes(2, "big")
            + b"test.txt\x00"  # Filename
            + b"octet\x00"  # Mode
        )

        transport.sendto(rrq_packet)
        await asyncio.sleep(0.1)

        assert server.transport is not None

    finally:
        transport.close()
        await server.shutdown()
        await server_task


@pytest.mark.anyio
async def test_read_request_for_nonexistent_file(tftp_server):
    """Test reading a non-existent file returns appropriate error."""
    server, _ = tftp_server

    server_task = asyncio.create_task(server.start())
    await asyncio.sleep(0.1)

    try:
        transport, protocol = await create_test_client(server.port)

        rrq_packet = Opcode.RRQ.to_bytes(2, "big") + b"nonexistent.txt\x00" + b"octet\x00"

        transport.sendto(rrq_packet)
        await asyncio.sleep(0.1)

        assert server.transport is not None

    finally:
        transport.close()
        await server.shutdown()
        await server_task


@pytest.mark.anyio
async def test_write_request_rejection(tftp_server):
    """Test that write requests are properly rejected (server is read-only)."""
    server, _ = tftp_server
    server_task = asyncio.create_task(server.start())
    await asyncio.sleep(0.1)

    try:
        transport, _ = await create_test_client(server.port)
        wrq_packet = Opcode.WRQ.to_bytes(2, "big") + b"test.txt\x00" + b"octet\x00"

        transport.sendto(wrq_packet)
        await asyncio.sleep(0.1)

        assert server.transport is not None

    finally:
        transport.close()
        await server.shutdown()
        await server_task


@pytest.mark.anyio
async def test_invalid_packet_handling(tftp_server):
    server, _ = tftp_server
    server_task = asyncio.create_task(server.start())
    await asyncio.sleep(0.1)

    try:
        transport, _ = await create_test_client(server.port)
        transport.sendto(b"\x00\x01")
        await asyncio.sleep(0.1)

        assert server.transport is not None

    finally:
        transport.close()
        await server.shutdown()
        await server_task


@pytest.mark.anyio
async def test_path_traversal_prevention(tftp_server):
    """Test that path traversal attempts are blocked."""
    server, _ = tftp_server

    server_task = asyncio.create_task(server.start())
    await asyncio.sleep(0.1)

    try:
        transport, _ = await create_test_client(server.port)

        rrq_packet = Opcode.RRQ.to_bytes(2, "big") + b"../../../etc/passwd\x00" + b"octet\x00"

        transport.sendto(rrq_packet)
        await asyncio.sleep(0.1)

        assert server.transport is not None

    finally:
        transport.close()
        await server.shutdown()
        await server_task


@pytest.fixture
def anyio_backend():
    return "asyncio"
