from unittest.mock import AsyncMock

import grpc
import pytest
from jumpstarter_protocol import router_pb2

from .router import RouterStream


@pytest.fixture
def mock_client_context():
    ctx = AsyncMock(spec=grpc.aio.StreamStreamCall)
    ctx.done = lambda: False
    return ctx


@pytest.fixture
def client_stream(mock_client_context):
    stream = RouterStream(context=mock_client_context)
    return stream


class TestSendEofSkipsWriteWhenDone:
    @pytest.mark.anyio
    async def test_send_eof_writes_goaway_when_context_active(self, client_stream, mock_client_context):
        await client_stream.send_eof()

        mock_client_context.write.assert_awaited_once()
        frame = mock_client_context.write.call_args[0][0]
        assert frame.frame_type == router_pb2.FRAME_TYPE_GOAWAY

    @pytest.mark.anyio
    async def test_send_eof_skips_write_when_context_done(self, client_stream, mock_client_context):
        mock_client_context.done = lambda: True

        await client_stream.send_eof()

        mock_client_context.write.assert_not_awaited()
