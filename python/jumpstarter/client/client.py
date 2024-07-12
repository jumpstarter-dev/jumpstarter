from jumpstarter.v1 import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
    router_pb2,
    router_pb2_grpc,
)
from jumpstarter.drivers import DriverStub
from google.protobuf import empty_pb2
from dataclasses import dataclass
import jumpstarter.drivers as drivers
import contextlib
import anyio


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
    async def Stream(self, device):
        client_to_device_tx, client_to_device_rx = anyio.create_memory_object_stream[
            bytes
        ](32)
        device_to_client_tx, device_to_client_rx = anyio.create_memory_object_stream[
            bytes
        ](32)

        async def client_to_device():
            async for payload in client_to_device_rx:
                yield router_pb2.StreamRequest(payload=payload)

        async def device_to_client():
            async for frame in self.router.Stream(
                client_to_device(), metadata=(("device", device.uuid),)
            ):
                await device_to_client_tx.send(frame.payload)

        async with anyio.create_task_group() as tg:
            tg.start_soon(device_to_client)
            try:
                yield anyio.streams.stapled.StapledObjectStream(
                    client_to_device_tx, device_to_client_rx
                )
            finally:
                tg.cancel_scope.cancel()

    async def Forward(
        self,
        listener,
        device,
    ):
        async def handle(client):
            async def rx():
                async for payload in client:
                    yield router_pb2.StreamRequest(payload=payload)

            async for frame in self.router.Stream(
                rx(), metadata=(("device", device.uuid),)
            ):
                await client.send(frame.payload)

        await listener.serve(handle)
