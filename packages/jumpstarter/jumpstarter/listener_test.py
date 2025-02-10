# These tests are flaky
# https://github.com/grpc/grpc/issues/25364

from uuid import uuid4

import grpc
import pytest
from anyio import create_task_group
from anyio.from_thread import start_blocking_portal
from jumpstarter_driver_power.driver import MockPower

from jumpstarter.client import Lease
from jumpstarter.common import MetadataFilter
from jumpstarter.common.grpc import aio_secure_channel, ssl_channel_credentials
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.config.tls import TLSConfigV1Alpha1
from jumpstarter.exporter import Exporter, Session

pytestmark = pytest.mark.anyio


@pytest.mark.xfail(raises=Exception)
async def test_router(mock_controller, monkeypatch):
    uuid = uuid4()

    async def handle_async(stream):
        async with connect_router_stream(mock_controller, str(uuid), stream, TLSConfigV1Alpha1(insecure=True)):
            pass

    with Session(
        uuid=uuid,
        labels={},
        root_device=MockPower(),
    ) as session:
        async with session.serve_unix_async() as path:
            async with create_task_group() as tg:
                tg.start_soon(
                    Exporter._Exporter__handle, None, path, mock_controller, str(uuid), TLSConfigV1Alpha1(insecure=True)
                )
                with start_blocking_portal() as portal:
                    lease = Lease(
                        channel=grpc.aio.insecure_channel("grpc.invalid"),
                        metadata_filter=MetadataFilter(),
                        portal=portal,
                        allow=[],
                        unsafe=True,
                    )

                    monkeypatch.setattr(lease, "handle_async", handle_async)

                    async with lease.connect_async() as client:
                        await client.call_async("on")
                tg.cancel_scope.cancel()


@pytest.mark.xfail(raises=Exception)
async def test_unsatisfiable(mock_controller):
    with start_blocking_portal() as portal:
        with pytest.raises(ValueError):
            async with Lease(
                channel=aio_secure_channel(
                    mock_controller,
                    ssl_channel_credentials(mock_controller, tls_config=TLSConfigV1Alpha1(insecure=True)),
                ),
                metadata_filter=MetadataFilter(labels={"unsatisfiable": "true"}),
                portal=portal,
                allow=[],
                unsafe=True,
            ):
                pass


@pytest.mark.xfail(raises=Exception)
async def test_controller(mock_controller):
    uuid = uuid4()

    async with Exporter(
        channel_factory=lambda: aio_secure_channel(
            mock_controller, ssl_channel_credentials(mock_controller, tls_config=TLSConfigV1Alpha1(insecure=True))
        ),
        uuid=uuid,
        labels={},
        device_factory=lambda: MockPower(),
    ) as exporter:
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve)

            with start_blocking_portal() as portal:
                async with Lease(
                    channel=aio_secure_channel(
                        mock_controller,
                        ssl_channel_credentials(mock_controller, tls_config=TLSConfigV1Alpha1(insecure=True)),
                    ),
                    metadata_filter=MetadataFilter(),
                    portal=portal,
                    allow=[],
                    unsafe=True,
                ) as lease:
                    async with lease.connect_async() as client:
                        await client.call_async("on")
                        # test concurrent connections
                        async with lease.connect_async() as client2:
                            await client2.call_async("on")

                    async with lease.connect_async() as client:
                        await client.call_async("on")

            tg.cancel_scope.cancel()
