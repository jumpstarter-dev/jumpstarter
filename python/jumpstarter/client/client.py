from collections import OrderedDict
from importlib import import_module
from uuid import UUID

from google.protobuf import empty_pb2

from jumpstarter.client import DriverClient
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

        # reference: https://docs.djangoproject.com/en/5.0/_modules/django/utils/module_loading/#import_string
        module_path, class_name = labels["jumpstarter.dev/client"].rsplit(".", 1)
        client_class = getattr(import_module(module_path), class_name)
        client = client_class(uuid=uuid, labels=labels, channel=channel, portal=portal)

        clients[uuid] = client

        if report.parent_uuid != "":
            clients[UUID(report.parent_uuid)] |= client

    return clients.popitem(last=False)[1]
