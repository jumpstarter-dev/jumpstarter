from jumpstarter.v1 import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
    router_pb2_grpc,
)
from jumpstarter.drivers.composite import CompositeClient
from jumpstarter.drivers.power import PowerClient
from jumpstarter.drivers.network import NetworkClient
from jumpstarter.drivers.storage import StorageMuxClient
from jumpstarter.drivers import DriverClient
from jumpstarter.common.streams import forward_client_stream
from google.protobuf import empty_pb2
from dataclasses import dataclass
from uuid import UUID, uuid4
from anyio.streams.file import FileReadStream
from collections import OrderedDict
import contextlib
import anyio


def ClientFromReports(
    reports: list[jumpstarter_pb2.DriverInstanceReport],
    channel,
) -> DriverClient:
    clients = OrderedDict()

    for report in reports:
        uuid = UUID(report.uuid)
        labels = report.labels
        match report.labels["jumpstarter.dev/interface"]:
            case "power":
                client = PowerClient(uuid=uuid, labels=labels, channel=channel)
            case "composite":
                client = CompositeClient(uuid=uuid, labels=labels, channel=channel)
            case "network":
                client = NetworkClient(uuid=uuid, labels=labels, channel=channel)
            case "storage_mux":
                client = StorageMuxClient(uuid=uuid, labels=labels, channel=channel)
            case _:
                raise ValueError
        clients[uuid] = client

        if report.parent_uuid != "":
            clients[UUID(report.parent_uuid)][uuid] = client

    return clients.popitem(last=False)[1]


@dataclass
class Client:
    stub: jumpstarter_pb2_grpc.ExporterServiceStub

    def __init__(self, channel):
        self.channel = channel
        self.stub = jumpstarter_pb2_grpc.ExporterServiceStub(channel)
        self.router = router_pb2_grpc.RouterServiceStub(channel)

    async def sync(self):
        self.root = ClientFromReports(
            (await self.stub.GetReport(empty_pb2.Empty())).reports, self.channel
        )

    @contextlib.asynccontextmanager
    async def Resource(
        self,
        stream,
    ):
        uuid = uuid4()

        async def handle(stream):
            async with stream:
                await forward_client_stream(
                    self.router, stream, {"kind": "resource", "uuid": str(uuid)}.items()
                )

        async with anyio.create_task_group() as tg:
            tg.start_soon(handle, stream)
            try:
                yield str(uuid)
            finally:
                tg.cancel_scope.cancel()

    @contextlib.asynccontextmanager
    async def LocalFile(
        self,
        filepath,
    ):
        async with await FileReadStream.from_path(filepath) as file:
            async with self.Resource(file) as uuid:
                yield uuid
