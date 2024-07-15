from jumpstarter.v1 import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
    router_pb2,
    router_pb2_grpc,
)
from jumpstarter.common.streams import create_memory_stream
from jumpstarter.drivers import DriverStub
from google.protobuf import empty_pb2
from dataclasses import dataclass
from uuid import uuid4
from anyio.streams.file import FileReadStream
import jumpstarter.drivers as drivers
import contextlib
import anyio
import grpc


@dataclass
class Client:
    stub: jumpstarter_pb2_grpc.ExporterServiceStub

    def __init__(self, channel):
        self.stub = jumpstarter_pb2_grpc.ExporterServiceStub(channel)
        self.router = router_pb2_grpc.RouterServiceStub(channel)

    async def sync(self):
        devices = dict()
        for device in (await self.GetReport()).device_report:
            stub = self.GetDevice(device)
            devices[stub.uuid] = stub
            if device.parent_device_uuid == "":
                setattr(self, stub.labels["jumpstarter.dev/name"], stub)
            else:
                setattr(
                    devices[device.parent_device_uuid],
                    stub.labels["jumpstarter.dev/name"],
                    stub,
                )

    async def GetReport(self):
        return await self.stub.GetReport(empty_pb2.Empty())

    def GetDevice(self, report: jumpstarter_pb2.DeviceReport):
        base = drivers.get(report.driver_interface)

        class stub_class(DriverStub, base=base):
            pass

        return stub_class(stub=self.stub, uuid=report.device_uuid, labels=report.labels)

    @contextlib.asynccontextmanager
    async def Stream(self, device=None, stream_id=None):
        client_stream, device_stream = create_memory_stream()

        async def client_to_device():
            async for payload in device_stream:
                yield router_pb2.StreamRequest(payload=payload)

        async def device_to_client():
            metadata = []
            if device is not None:
                metadata.append(("device", device.uuid))
            if stream_id is not None:
                metadata.append(("stream_id", stream_id))
            async for frame in self.router.Stream(
                client_to_device(),
                metadata=metadata,
            ):
                await device_stream.send(frame.payload)

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(device_to_client)
                try:
                    yield client_stream
                finally:
                    await client_stream.send_eof()
        except* grpc.aio.AioRpcError:
            # TODO: handle connection failure
            pass

    @contextlib.asynccontextmanager
    async def Forward(
        self,
        listener,
        device,
    ):
        async def forward(rx, tx):
            async for payload in tx:
                await rx.send(payload)

        async def handle(client):
            async with client:
                async with self.Stream(device) as stream:
                    async with anyio.create_task_group() as tg:
                        tg.start_soon(forward, client, stream)
                        tg.start_soon(forward, stream, client)

        async with anyio.create_task_group() as tg:
            tg.start_soon(listener.serve, handle)
            try:
                yield
            finally:
                tg.cancel_scope.cancel()

    @contextlib.asynccontextmanager
    async def LocalFile(
        self,
        filepath,
    ):
        stream_id = uuid4()

        async def handle():
            async with self.Stream(stream_id=str(stream_id)) as stream:
                async with await FileReadStream.from_path(filepath) as f:
                    async for chunk in f:
                        await stream.send(chunk)

        async with anyio.create_task_group() as tg:
            tg.start_soon(handle)
            try:
                yield str(stream_id)
            finally:
                tg.cancel_scope.cancel()
