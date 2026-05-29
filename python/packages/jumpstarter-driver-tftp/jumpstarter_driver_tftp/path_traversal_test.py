from unittest.mock import AsyncMock, MagicMock

import pytest

from jumpstarter_driver_tftp.server import TftpErrorCode, TftpServer, TftpServerProtocol


@pytest.fixture
def server():
    operator = AsyncMock()
    return TftpServer(
        host="127.0.0.1",
        port=0,
        operator=operator,  # ty: ignore[invalid-argument-type]
    )


@pytest.fixture
def protocol(server):
    proto = TftpServerProtocol(server)
    proto.transport = MagicMock()
    return proto


class TestResolveAndValidatePath:
    @pytest.mark.asyncio
    async def test_rejects_dot_dot_path(self, protocol):
        result = await protocol._resolve_and_validate_path("..", ("127.0.0.1", 12345))
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_dot_dot_prefix(self, protocol):
        result = await protocol._resolve_and_validate_path("../etc/passwd", ("127.0.0.1", 12345))
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_dot_dot_in_middle(self, protocol):
        result = await protocol._resolve_and_validate_path("subdir/../../../etc/passwd", ("127.0.0.1", 12345))
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_dot_dot_at_end(self, protocol):
        result = await protocol._resolve_and_validate_path("subdir/..", ("127.0.0.1", 12345))
        assert result is None

    @pytest.mark.asyncio
    async def test_allows_valid_filename(self, protocol, server):
        stat_result = MagicMock()
        stat_result.mode.is_file.return_value = True
        server.operator.stat = AsyncMock(return_value=stat_result)

        result = await protocol._resolve_and_validate_path("boot.img", ("127.0.0.1", 12345))
        assert result == "boot.img"

    @pytest.mark.asyncio
    async def test_allows_filename_containing_dots(self, protocol, server):
        stat_result = MagicMock()
        stat_result.mode.is_file.return_value = True
        server.operator.stat = AsyncMock(return_value=stat_result)

        result = await protocol._resolve_and_validate_path("file..name.txt", ("127.0.0.1", 12345))
        assert result == "file..name.txt"

    @pytest.mark.asyncio
    async def test_rejects_absolute_path(self, protocol):
        result = await protocol._resolve_and_validate_path("/etc/passwd", ("127.0.0.1", 12345))
        assert result is None

    @pytest.mark.asyncio
    async def test_sends_access_violation_on_traversal(self, protocol):
        await protocol._resolve_and_validate_path("../secret", ("127.0.0.1", 12345))
        protocol.transport.sendto.assert_called_once()
        sent_data = protocol.transport.sendto.call_args[0][0]
        error_code = int.from_bytes(sent_data[2:4], "big")
        assert error_code == TftpErrorCode.ACCESS_VIOLATION
