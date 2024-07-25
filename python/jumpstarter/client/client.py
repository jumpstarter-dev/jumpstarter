from collections import OrderedDict
from importlib import import_module
from uuid import UUID

from google.protobuf import empty_pb2

from jumpstarter.drivers import DriverClient
from jumpstarter.v1 import (
    jumpstarter_pb2_grpc,
)


async def client_from_channel(
    channel,
    portal,
) -> DriverClient:
    clients = OrderedDict()

    response = await jumpstarter_pb2_grpc.ExporterServiceStub(channel).GetReport(empty_pb2.Empty())

    for report in response.reports:
        uuid = UUID(report.uuid)
        labels = report.labels

        client_module = import_module(labels["jumpstarter.dev/client_module"])
        client_class = getattr(client_module, labels["jumpstarter.dev/client_class"])
        client = client_class(uuid=uuid, labels=labels, channel=channel, portal=portal)

        clients[uuid] = client

        if report.parent_uuid != "":
            clients[UUID(report.parent_uuid)] |= client

    return clients.popitem(last=False)[1]
