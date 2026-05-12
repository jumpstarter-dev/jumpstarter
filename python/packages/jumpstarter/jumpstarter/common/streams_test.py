import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

import grpc
import pytest
from grpc.aio import AioRpcError

from jumpstarter.common.streams import connect_router_stream


class TestConnectRouterStreamChannelReady:
    """Tests for the channel_ready timeout logic in connect_router_stream."""

    @pytest.mark.anyio
    async def test_raises_unavailable_on_channel_ready_timeout(self):
        """When channel_ready() times out, an AioRpcError with UNAVAILABLE should be raised."""
        mock_channel = Mock()

        # Make channel_ready() return a coroutine that never completes
        async def hang_forever():
            await asyncio.sleep(999)

        mock_channel.channel_ready = Mock(return_value=hang_forever())

        @asynccontextmanager
        async def fake_secure_channel(*args, **kwargs):
            yield mock_channel

        with (
            patch("jumpstarter.common.streams.ssl_channel_credentials", new_callable=AsyncMock),
            patch("jumpstarter.common.streams.aio_secure_channel", side_effect=fake_secure_channel),
            patch("grpc.composite_channel_credentials", return_value=Mock()),
            patch("grpc.access_token_call_credentials", return_value=Mock()),
        ):
            with pytest.raises(AioRpcError) as exc_info:
                async with connect_router_stream(
                    "endpoint:443", "token", Mock(), Mock(), {}, channel_ready_timeout=0.01
                ):
                    pass  # pragma: no cover

            assert exc_info.value.code() == grpc.StatusCode.UNAVAILABLE
            assert "Timed out" in str(exc_info.value.details())

    @pytest.mark.anyio
    async def test_proceeds_when_channel_ready_succeeds(self):
        """When channel_ready() succeeds quickly, the stream should be set up normally."""
        mock_channel = Mock()

        # channel_ready() resolves immediately
        async def ready_immediately():
            pass

        mock_channel.channel_ready = Mock(return_value=ready_immediately())

        mock_context = Mock()

        @asynccontextmanager
        async def fake_secure_channel(*args, **kwargs):
            yield mock_channel

        @asynccontextmanager
        async def fake_router_stream(*args, **kwargs):
            yield Mock()

        @asynccontextmanager
        async def fake_forward(*args, **kwargs):
            yield

        with (
            patch("jumpstarter.common.streams.ssl_channel_credentials", new_callable=AsyncMock),
            patch("jumpstarter.common.streams.aio_secure_channel", side_effect=fake_secure_channel),
            patch("grpc.composite_channel_credentials", return_value=Mock()),
            patch("grpc.access_token_call_credentials", return_value=Mock()),
            patch("jumpstarter.common.streams.router_pb2_grpc.RouterServiceStub") as mock_stub_cls,
            patch("jumpstarter.common.streams.RouterStream", side_effect=fake_router_stream),
            patch("jumpstarter.common.streams.forward_stream", side_effect=fake_forward),
        ):
            mock_stub = Mock()
            mock_stub.Stream.return_value = mock_context
            mock_stub_cls.return_value = mock_stub

            async with connect_router_stream(
                "endpoint:443", "token", Mock(), Mock(), {}, channel_ready_timeout=5
            ):
                pass  # Successfully entered the context

            mock_channel.channel_ready.assert_called_once()
