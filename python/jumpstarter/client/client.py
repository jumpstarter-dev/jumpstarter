from collections import OrderedDict
from uuid import UUID

from google.protobuf import empty_pb2

from jumpstarter.drivers import DriverClient
from jumpstarter.drivers.composite import CompositeClient
from jumpstarter.drivers.network import NetworkClient
from jumpstarter.drivers.power import PowerClient
from jumpstarter.drivers.storage import StorageMuxClient
from jumpstarter.v1 import (
    jumpstarter_pb2_grpc,
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
            clients[UUID(report.parent_uuid)] |= client

    return clients.popitem(last=False)[1]
