import contextlib
from collections import OrderedDict
from dataclasses import dataclass
from uuid import UUID, uuid4

import anyio
from anyio.streams.file import FileReadStream
from google.protobuf import empty_pb2

from jumpstarter.common.streams import forward_client_stream
from jumpstarter.drivers import DriverClient
from jumpstarter.drivers.composite import CompositeClient
from jumpstarter.drivers.network import NetworkClient
from jumpstarter.drivers.power import PowerClient
from jumpstarter.drivers.storage import StorageMuxClient
from jumpstarter.v1 import (
    jumpstarter_pb2_grpc,
    router_pb2_grpc,
)


async def client_from_channel(
    channel,
) -> DriverClient:
    clients = OrderedDict()

    response = await jumpstarter_pb2_grpc.ExporterServiceStub(channel).GetReport(empty_pb2.Empty())

    for report in response.reports:
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
        self.root = await client_from_channel(self.channel)
