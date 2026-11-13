import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import pytest
from anyio import (
    BrokenResourceError,
    Event,
    create_connected_udp_socket,
    create_task_group,
    create_udp_socket,
    fail_after,
    move_on_after,
    sleep,
)
from opendal import AsyncOperator

from jumpstarter_driver_tftp import server as server_module
from jumpstarter_driver_tftp.server import Opcode, TftpErrorCode, TftpServer


@pytest.fixture
async def tftp_server():
    with tempfile.TemporaryDirectory() as temp_dir:
        test_file_path = Path(temp_dir) / "test.txt"
        test_file_path.write_text("Hello, TFTP!")

        server = TftpServer(host="127.0.0.1", port=0, operator=AsyncOperator("fs", root=str(temp_dir)))
        async with create_task_group() as tg:
            tg.start_soon(server.start)
            with fail_after(1):
                await server.ready_event.wait()
            assert server.address is not None

            yield server, temp_dir, server.address[1]

            await server.shutdown()


async def create_test_client(server_port):
    return await create_connected_udp_socket("127.0.0.1", server_port)


@pytest.mark.anyio
async def test_server_startup_and_shutdown(tftp_server):
    """Test that server starts up and shuts down cleanly."""
    server, _temp_dir, _server_port = tftp_server

    async with create_task_group() as tg:
        tg.start_soon(server.start)
        await server.ready_event.wait()

        await server.shutdown()

    assert True


@pytest.mark.anyio
async def test_read_request_for_existing_file(tftp_server):
    """Test reading an existing file from the server."""
    server, _temp_dir, server_port = tftp_server

    await server.ready_event.wait()

    try:
        transport = await create_test_client(server_port)

        rrq_packet = (
            Opcode.RRQ.to_bytes(2, "big")
            + b"test.txt\x00"  # filename
            + b"octet\x00"  # mode
        )

        await transport.send(rrq_packet)
        await server.ready_event.wait()

        assert server.sock is not None

    finally:
        await transport.aclose()
        await server.shutdown()


@pytest.mark.anyio
async def test_read_request_for_nonexistent_file(tftp_server):
    """Test reading a non-existent file returns appropriate error."""
    server, _temp_dir, server_port = tftp_server


    try:
        transport = await create_test_client(server_port)

        rrq_packet = Opcode.RRQ.to_bytes(2, "big") + b"nonexistent.txt\x00" + b"octet\x00"

        await transport.send(rrq_packet)
        assert server.sock is not None

    finally:
        await transport.aclose()
        await server.shutdown()


@pytest.mark.anyio
async def test_write_request_rejection(tftp_server):
    """Test that write requests are properly rejected (server is read-only)."""
    server, _temp_dir, server_port = tftp_server

    try:
        transport = await create_test_client(server_port)
        wrq_packet = Opcode.WRQ.to_bytes(2, "big") + b"test.txt\x00" + b"octet\x00"

        await transport.send(wrq_packet)

        assert server.sock is not None

    finally:
        await transport.aclose()
        await server.shutdown()


@pytest.mark.anyio
async def test_invalid_packet_handling(tftp_server):
    server, _temp_dir, server_port = tftp_server
    await server.ready_event.wait()

    try:
        transport = await create_test_client(server_port)
        await transport.send(b"\x00\x01")

        assert server.sock is not None

    finally:
        await transport.aclose()
        await server.shutdown()


@pytest.mark.anyio
async def test_path_traversal_prevention(tftp_server):
    """Test that path traversal attempts are blocked."""
    server, _temp_dir, server_port = tftp_server

    await server.ready_event.wait()

    try:
        transport = await create_test_client(server_port)

        rrq_packet = Opcode.RRQ.to_bytes(2, "big") + b"../../../etc/passwd\x00" + b"octet\x00"

        await transport.send(rrq_packet)

        assert server.sock is not None

    finally:
        await transport.aclose()
        await server.shutdown()


@pytest.mark.anyio
async def test_options_negotiation(tftp_server):
    """Test that options (blksize, timeout) are properly negotiated."""
    server, _temp_dir, server_port = tftp_server
    await server.ready_event.wait()

    try:
        transport = await create_test_client(server_port)

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

        await transport.send(rrq_packet)

        assert server.sock is not None

    finally:
        await transport.aclose()
        await server.shutdown()


@pytest.mark.anyio
async def test_retry_mechanism(tftp_server):
    server, _, server_port = tftp_server

    # make the test faster
    server.timeout = 1

    rrq_packet = Opcode.RRQ.to_bytes(2, "big") + b"test.txt\x00" + b"octet\x00"

    async with await create_udp_socket(local_host="127.0.0.1") as client:
        await client.sendto(rrq_packet, "127.0.0.1", server_port)

        received_packets = []
        with move_on_after(server.timeout * 2):
            async for packet, _ in client:
                received_packets.append(packet)

    data_packets = [p for p in received_packets if p[0:2] == Opcode.DATA.to_bytes(2, "big")]

    assert len(data_packets) > 1, "Server should have retried sending DATA packet"

    block_numbers = {int.from_bytes(p[2:4], "big") for p in data_packets}
    assert len(block_numbers) == 1, "All retried packets should be for the same block"
    assert 1 in block_numbers, "First block number should be 1"


@pytest.mark.anyio
async def test_invalid_options_handling(tftp_server):
    server, _temp_dir, server_port = tftp_server
    await server.ready_event.wait()

    try:
        transport = await create_test_client(server_port)

        rrq_packet = (
            Opcode.RRQ.to_bytes(2, "big")
            + b"test.txt\x00"
            + b"octet\x00"
            + b"blksize\x00"
            + b"invalid\x00"
            + b"timeout\x00"
            + b"999999\x00"
        )

        await transport.send(rrq_packet)

        assert server.sock is not None

    finally:
        await transport.aclose()
        await server.shutdown()


TIMEOUT = 0.3


def rrq(filename: str, mode: str = "octet", **options: str) -> bytes:
    packet = Opcode.RRQ.to_bytes(2, "big") + f"{filename}\0{mode}\0".encode()
    for name, value in options.items():
        packet += f"{name}\0{value}\0".encode()
    return packet


def ack(block: int) -> bytes:
    return Opcode.ACK.to_bytes(2, "big") + block.to_bytes(2, "big")


def error(code: TftpErrorCode, message: str) -> bytes:
    return Opcode.ERROR.to_bytes(2, "big") + code.to_bytes(2, "big") + message.encode() + b"\0"


def opcode(packet: bytes) -> Opcode:
    return Opcode(int.from_bytes(packet[:2], "big"))


def second_field(packet: bytes) -> int:
    """The block number of DATA and ACK packets, or the error code of ERROR packets."""
    return int.from_bytes(packet[2:4], "big")


def oack_options(packet: bytes) -> dict[str, str]:
    parts = packet[2:].split(b"\0")[:-1]
    return {parts[i].decode(): parts[i + 1].decode() for i in range(0, len(parts), 2)}


def _server(root, host: str = "127.0.0.1", operator: object = None) -> TftpServer:
    return TftpServer(
        host=host,
        port=0,
        operator=cast(AsyncOperator, operator or AsyncOperator("fs", root=str(root))),
        timeout=TIMEOUT,
        retries=3,
    )


@asynccontextmanager
async def _serving(server: TftpServer):
    async with create_task_group() as tg:
        tg.start_soon(server.start)
        with fail_after(5):
            while server.address is None:
                await sleep(0.01)
        try:
            yield server.address[1]
        finally:
            await server.shutdown()


@pytest.fixture
async def tftp(tmp_path):
    async with _serving(_server(tmp_path)) as port:
        yield tmp_path, port


@pytest.fixture
async def client():
    async with await create_udp_socket(local_host="127.0.0.1") as sock:
        yield sock


async def reply_to(client, port: int, packet: bytes) -> bytes:
    await client.sendto(packet, "127.0.0.1", port)
    with fail_after(5):
        reply, _ = await client.receive()
    return reply


async def download(client, port: int, filename: str, host: str = "127.0.0.1", **options: str) -> bytes:
    await client.sendto(rrq(filename, **options), host, port)
    block_size = int(options.get("blksize", 512))
    data = b""
    with fail_after(5):
        packet, transfer = await client.receive()
        if options:
            assert opcode(packet) == Opcode.OACK
            await client.sendto(ack(0), *transfer)
            packet, transfer = await client.receive()
        while True:
            assert opcode(packet) == Opcode.DATA, packet
            data += packet[4:]
            await client.sendto(ack(second_field(packet)), *transfer)
            if len(packet) - 4 < block_size:
                return data
            packet, transfer = await client.receive()


@pytest.mark.anyio
@pytest.mark.parametrize("size", [0, 1, 511, 512, 1024, 1300])
async def test_files_of_any_size_download_intact(tftp, client, size):
    root, port = tftp
    (root / "file.bin").write_bytes(bytes(i % 251 for i in range(size)))

    assert await download(client, port, "file.bin") == (root / "file.bin").read_bytes()


@pytest.mark.anyio
async def test_blksize_and_timeout_are_acknowledged(tftp, client):
    root, port = tftp
    (root / "file.bin").write_bytes(b"x" * 3000)

    await client.sendto(rrq("file.bin", blksize="1024", timeout="2"), "127.0.0.1", port)
    with fail_after(5):
        oack, transfer = await client.receive()
        await client.sendto(ack(0), *transfer)
        packet, _ = await client.receive()

    assert oack_options(oack) == {"blksize": "1024", "timeout": "2"}
    assert (opcode(packet), second_field(packet), len(packet) - 4) == (Opcode.DATA, 1, 1024)


@pytest.mark.anyio
@pytest.mark.parametrize("options", [{"blksize": "1024"}, {"blksize": "512"}, {"timeout": "2"}])
async def test_a_single_requested_option_is_acknowledged(tftp, client, options):
    root, port = tftp
    (root / "file.bin").write_bytes(b"x" * 3000)

    assert await download(client, port, "file.bin", **options) == b"x" * 3000


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("packet", "code"),
    [
        (rrq("missing.txt"), TftpErrorCode.FILE_NOT_FOUND),
        (rrq("boot"), TftpErrorCode.FILE_NOT_FOUND),
        (rrq(".."), TftpErrorCode.ACCESS_VIOLATION),
        (rrq("file.bin", mode="mail"), TftpErrorCode.ILLEGAL_OPERATION),
        (Opcode.RRQ.to_bytes(2, "big") + b"file.bin", TftpErrorCode.NOT_DEFINED),
        (Opcode.WRQ.to_bytes(2, "big") + b"file.bin\0octet\0", TftpErrorCode.ILLEGAL_OPERATION),
        (b"\x00\x09\x00\x00", TftpErrorCode.ILLEGAL_OPERATION),
        (b"\x00", TftpErrorCode.ILLEGAL_OPERATION),
    ],
)
async def test_bad_requests_get_an_error(tftp, client, packet, code):
    root, port = tftp
    (root / "boot").mkdir()

    reply = await reply_to(client, port, packet)

    assert (opcode(reply), second_field(reply)) == (Opcode.ERROR, code)


@pytest.mark.anyio
async def test_a_silent_client_gets_the_block_again(tftp, client):
    root, port = tftp
    (root / "file.bin").write_bytes(b"data")

    await client.sendto(rrq("file.bin"), "127.0.0.1", port)
    with fail_after(5):
        first, _ = await client.receive()
        again, _ = await client.receive()

    assert opcode(first) == Opcode.DATA
    assert again == first


class _StatsReturnTogether:
    """Delegates to *operator*, holding every stat() result until *count* of them are ready."""

    def __init__(self, operator: AsyncOperator, count: int):
        self._operator = operator
        self._count = count
        self._ready = 0
        self._all_ready = Event()

    async def stat(self, path: str):
        try:
            return await self._operator.stat(path)
        finally:
            self._ready += 1
            if self._ready >= self._count:
                self._all_ready.set()
            await self._all_ready.wait()

    def __getattr__(self, name: str):
        return getattr(self._operator, name)


@pytest.mark.anyio
async def test_refusals_due_at_the_same_time_all_arrive(tmp_path, client):
    (tmp_path / "boot").mkdir()
    requests = 4
    operator = _StatsReturnTogether(AsyncOperator("fs", root=str(tmp_path)), requests)

    async with _serving(_server(tmp_path, operator=operator)) as port:
        for _ in range(requests):
            await client.sendto(rrq("boot"), "127.0.0.1", port)
        with fail_after(5):
            replies = [(await client.receive())[0] for _ in range(requests)]

    assert {(opcode(reply), second_field(reply)) for reply in replies} == {(Opcode.ERROR, TftpErrorCode.FILE_NOT_FOUND)}


@pytest.mark.anyio
async def test_a_refusal_that_fails_to_send_leaves_the_server_up(tmp_path, client, monkeypatch):
    (tmp_path / "boot").mkdir()
    create_listener = server_module.create_udp_socket

    async def listener_whose_first_send_fails(**kwargs):
        sock = await create_listener(**kwargs)
        sendto = sock.sendto

        async def fail_once(*args):
            monkeypatch.setattr(sock, "sendto", sendto)
            raise BrokenResourceError

        monkeypatch.setattr(sock, "sendto", fail_once)
        return sock

    monkeypatch.setattr(server_module, "create_udp_socket", listener_whose_first_send_fails)

    async with _serving(_server(tmp_path)) as port:
        await client.sendto(rrq("boot"), "127.0.0.1", port)
        reply = await reply_to(client, port, rrq("boot"))

    assert (opcode(reply), second_field(reply)) == (Opcode.ERROR, TftpErrorCode.FILE_NOT_FOUND)


@pytest.mark.anyio
async def test_a_duplicate_ack_is_not_answered_with_a_retransmission(tftp, client):
    root, port = tftp
    (root / "file.bin").write_bytes(b"x" * 1000)

    await client.sendto(rrq("file.bin", timeout="2"), "127.0.0.1", port)
    with fail_after(5):
        _, transfer = await client.receive()
        await client.sendto(ack(0), *transfer)
        await client.receive()
        await client.sendto(ack(1), *transfer)
        packet, _ = await client.receive()
    assert second_field(packet) == 2

    await client.sendto(ack(1), *transfer)
    with move_on_after(0.5) as quiet:
        await client.receive()
    assert quiet.cancelled_caught


@pytest.mark.anyio
async def test_an_error_sent_to_the_server_is_not_answered(tftp, client):
    _, port = tftp

    await client.sendto(error(TftpErrorCode.NOT_DEFINED, "hello"), "127.0.0.1", port)

    with move_on_after(TIMEOUT) as quiet:
        await client.receive()
    assert quiet.cancelled_caught


@pytest.mark.anyio
async def test_a_client_error_ends_the_transfer(tftp, client):
    root, port = tftp
    (root / "file.bin").write_bytes(b"data")

    await client.sendto(rrq("file.bin"), "127.0.0.1", port)
    with fail_after(5):
        _, transfer = await client.receive()
    await client.sendto(error(TftpErrorCode.NOT_DEFINED, "user abort"), *transfer)

    with move_on_after(TIMEOUT * 2) as quiet:
        await client.receive()
    assert quiet.cancelled_caught


@pytest.mark.anyio
async def test_ipv6_clients_are_served(tmp_path):
    (tmp_path / "file.bin").write_bytes(b"x" * 1000)
    try:
        client = await create_udp_socket(local_host="::1")
    except OSError:
        pytest.skip("no IPv6 loopback")  # ty: ignore[too-many-positional-arguments]

    async with client, _serving(_server(tmp_path, host="::1")) as port:
        assert await download(client, port, "file.bin", host="::1") == b"x" * 1000
