import pytest
from anyio import create_task_group, create_udp_socket, current_time, fail_after, move_on_after
from opendal import AsyncOperator

from jumpstarter_driver_tftp.server import Opcode, TftpErrorCode, TftpServer

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


def _server(root, host: str = "127.0.0.1") -> TftpServer:
    return TftpServer(host=host, port=0, operator=AsyncOperator("fs", root=str(root)), timeout=TIMEOUT, retries=3)


@pytest.fixture
async def tftp(tmp_path):
    async with create_task_group() as tg:
        port = await tg.start(_server(tmp_path).serve)
        yield tmp_path, port
        tg.cancel_scope.cancel()


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
async def test_ipv6_clients_are_served(tmp_path):
    (tmp_path / "file.bin").write_bytes(b"x" * 1000)
    try:
        client = await create_udp_socket(local_host="::1")
    except OSError:
        pytest.skip("no IPv6 loopback")  # ty: ignore[too-many-positional-arguments]

    async with client, create_task_group() as tg:
        port = await tg.start(_server(tmp_path, host="::1").serve)
        assert await download(client, port, "file.bin", host="::1") == b"x" * 1000
        tg.cancel_scope.cancel()


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
async def test_only_acceptable_options_are_acknowledged(tftp, client):
    root, port = tftp
    (root / "file.bin").write_bytes(b"x" * 3000)

    reply = await reply_to(client, port, rrq("file.bin", blksize="1024", timeout="999999"))

    assert oack_options(reply) == {"blksize": "1024"}


@pytest.mark.anyio
async def test_a_request_without_acceptable_options_starts_with_data(tftp, client):
    root, port = tftp
    (root / "file.bin").write_bytes(b"x" * 3000)

    reply = await reply_to(client, port, rrq("file.bin", blksize="invalid", tsize="0"))

    assert (opcode(reply), second_field(reply), len(reply) - 4) == (Opcode.DATA, 1, 512)


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
async def test_an_error_sent_to_the_server_is_not_answered(tftp, client):
    _, port = tftp

    await client.sendto(error(TftpErrorCode.NOT_DEFINED, "hello"), "127.0.0.1", port)

    with move_on_after(TIMEOUT) as quiet:
        await client.receive()
    assert quiet.cancelled_caught


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
async def test_stopping_the_server_ends_active_transfers(tmp_path, client):
    (tmp_path / "file.bin").write_bytes(b"data")

    async with create_task_group() as tg:
        port = await tg.start(_server(tmp_path).serve)
        await client.sendto(rrq("file.bin", timeout="2"), "127.0.0.1", port)
        with fail_after(5):
            await client.receive()
        stopped_at = current_time()
        tg.cancel_scope.cancel()

    assert current_time() - stopped_at < 1
