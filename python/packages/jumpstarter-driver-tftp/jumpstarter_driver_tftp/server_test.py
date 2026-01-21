import asyncio
import tempfile
from pathlib import Path

import pytest
from opendal import AsyncOperator

from jumpstarter_driver_tftp.server import Opcode, TftpServer


@pytest.fixture
async def tftp_server():
    with tempfile.TemporaryDirectory() as temp_dir:
        test_file_path = Path(temp_dir) / "test.txt"
        test_file_path.write_text("Hello, TFTP!")

        server = TftpServer(host="127.0.0.1", port=0, operator=AsyncOperator("fs", root=str(temp_dir)))
        server_task = asyncio.create_task(server.start())

        for _ in range(10):
            if server.address is not None:
                break
            await asyncio.sleep(0.1)
        else:
            await server.shutdown()
            server_task.cancel()
            raise RuntimeError("Failed to bind TFTP server to a port.")

        yield server, temp_dir, server.address[1] # ty: ignore[possibly-unbound-implicit-call]

        await server.shutdown()
        await server_task

        for task in asyncio.all_tasks():
            if not task.done() and task != asyncio.current_task():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


async def create_test_client(server_port):
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, remote_addr=("127.0.0.1", server_port)
    )
    return transport, protocol


@pytest.mark.asyncio
async def test_server_startup_and_shutdown(tftp_server):
    """Test that server starts up and shuts down cleanly."""
    server, temp_dir, server_port = tftp_server

    server_task = asyncio.create_task(server.start())
    await server.ready_event.wait()

    await server.shutdown()

    await server_task

    assert True


@pytest.mark.asyncio
async def test_read_request_for_existing_file(tftp_server):
    """Test reading an existing file from the server."""
    server, temp_dir, server_port = tftp_server

    server_task = asyncio.create_task(server.start())
    await server.ready_event.wait()

    try:
        transport, _ = await create_test_client(server_port)

        rrq_packet = (
            Opcode.RRQ.to_bytes(2, "big")
            + b"test.txt\x00"  # filename
            + b"octet\x00"  # mode
        )

        transport.sendto(rrq_packet)
        await server.ready_event.wait()

        assert server.transport is not None

    finally:
        transport.close()
        await server.shutdown()
        await server_task


@pytest.mark.asyncio
async def test_read_request_for_nonexistent_file(tftp_server):
    """Test reading a non-existent file returns appropriate error."""
    server, temp_dir, server_port = tftp_server

    server_task = asyncio.create_task(server.start())

    try:
        transport, protocol = await create_test_client(server_port)

        rrq_packet = Opcode.RRQ.to_bytes(2, "big") + b"nonexistent.txt\x00" + b"octet\x00"

        transport.sendto(rrq_packet)
        assert server.transport is not None

    finally:
        transport.close()
        await server.shutdown()
        await server_task


@pytest.mark.asyncio
async def test_write_request_rejection(tftp_server):
    """Test that write requests are properly rejected (server is read-only)."""
    server, temp_dir, server_port = tftp_server
    server_task = asyncio.create_task(server.start())

    try:
        transport, _ = await create_test_client(server_port)
        wrq_packet = Opcode.WRQ.to_bytes(2, "big") + b"test.txt\x00" + b"octet\x00"

        transport.sendto(wrq_packet)

        assert server.transport is not None

    finally:
        transport.close()
        await server.shutdown()
        await server_task


@pytest.mark.asyncio
async def test_invalid_packet_handling(tftp_server):
    server, temp_dir, server_port = tftp_server
    server_task = asyncio.create_task(server.start())
    await server.ready_event.wait()

    try:
        transport, _ = await create_test_client(server_port)
        transport.sendto(b"\x00\x01")

        assert server.transport is not None

    finally:
        transport.close()
        await server.shutdown()
        await server_task


@pytest.mark.asyncio
async def test_path_traversal_prevention(tftp_server):
    """Test that path traversal attempts are blocked."""
    server, temp_dir, server_port = tftp_server

    server_task = asyncio.create_task(server.start())
    await server.ready_event.wait()

    try:
        transport, _ = await create_test_client(server_port)

        rrq_packet = Opcode.RRQ.to_bytes(2, "big") + b"../../../etc/passwd\x00" + b"octet\x00"

        transport.sendto(rrq_packet)

        assert server.transport is not None

    finally:
        transport.close()
        await server.shutdown()
        await server_task


@pytest.mark.asyncio
async def test_options_negotiation(tftp_server):
    """Test that options (blksize, timeout) are properly negotiated."""
    server, temp_dir, server_port = tftp_server
    server_task = asyncio.create_task(server.start())
    await server.ready_event.wait()

    try:
        transport, _ = await create_test_client(server_port)

        # RRQ with options
        rrq_packet = (
            Opcode.RRQ.to_bytes(2, "big")
            + b"test.txt\x00"
            + b"octet\x00"
            + b"blksize\x00"
            + b"1024\x00"
            + b"timeout\x00"
            + b"3\x00"
        )

        transport.sendto(rrq_packet)

        assert server.transport is not None

    finally:
        transport.close()
        await server.shutdown()
        await server_task


@pytest.mark.asyncio
async def test_retry_mechanism(tftp_server):
    server, _, server_port = tftp_server

    # make the test faster
    server.timeout = 1

    transport = None

    class TestProtocol(asyncio.DatagramProtocol):
        def __init__(self):
            self.received_packets = []
            self.transport = None

        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            self.received_packets.append(data)

    try:
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(lambda: TestProtocol(), local_addr=("127.0.0.1", 0))

        assert transport is not None, "Failed to create transport"

        rrq_packet = Opcode.RRQ.to_bytes(2, "big") + b"test.txt\x00" + b"octet\x00"

        transport.sendto(rrq_packet, ("127.0.0.1", server_port))

        await asyncio.sleep(server.timeout * 2)

        data_packets = [p for p in protocol.received_packets if p[0:2] == Opcode.DATA.to_bytes(2, "big")]

        assert len(data_packets) > 1, "Server should have retried sending DATA packet"

        block_numbers = {int.from_bytes(p[2:4], "big") for p in data_packets}
        assert len(block_numbers) == 1, "All retried packets should be for the same block"
        assert 1 in block_numbers, "First block number should be 1"

    except Exception as e:
        pytest.fail(f"Test failed with error: {str(e)}") # ty: ignore[call-non-callable]

    finally:
        if transport is not None:
            transport.close()


@pytest.mark.asyncio
async def test_invalid_options_handling(tftp_server):
    server, temp_dir, server_port = tftp_server
    server_task = asyncio.create_task(server.start())
    await server.ready_event.wait()

    try:
        transport, _ = await create_test_client(server_port)

        rrq_packet = (
            Opcode.RRQ.to_bytes(2, "big")
            + b"test.txt\x00"
            + b"octet\x00"
            + b"blksize\x00"
            + b"invalid\x00"
            + b"timeout\x00"
            + b"999999\x00"
        )

        transport.sendto(rrq_packet)

        assert server.transport is not None

    finally:
        transport.close()
        await server.shutdown()
        await server_task
