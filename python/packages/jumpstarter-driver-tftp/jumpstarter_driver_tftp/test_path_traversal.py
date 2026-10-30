from unittest.mock import AsyncMock, MagicMock

import pytest

from jumpstarter_driver_tftp.server import TftpErrorCode, TftpRequestError, TftpServer


@pytest.fixture
def server():
    return TftpServer(host="127.0.0.1", port=0, operator=AsyncMock())


def _a_file():
    stat = MagicMock()
    stat.mode.is_file.return_value = True
    return stat


class TestResolveAndValidatePath:
    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "filename", ["..", "../etc/passwd", "subdir/../../../etc/passwd", "subdir/..", "/etc/passwd"]
    )
    async def test_rejects_paths_outside_the_root(self, server, filename):
        with pytest.raises(TftpRequestError) as rejected:
            await server._resolve_and_validate_path(filename)

        assert rejected.value.code == TftpErrorCode.ACCESS_VIOLATION
        server.operator.stat.assert_not_called()

    @pytest.mark.anyio
    @pytest.mark.parametrize("filename", ["boot.img", "file..name.txt"])
    async def test_allows_files_in_the_root(self, server, filename):
        server.operator.stat = AsyncMock(return_value=_a_file())

        assert await server._resolve_and_validate_path(filename) == filename
