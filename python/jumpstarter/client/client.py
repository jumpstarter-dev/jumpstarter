from collections import OrderedDict, defaultdict
from graphlib import TopologicalSorter
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
    topo = defaultdict(list)
    reports = {}
    clients = OrderedDict()

    response = await jumpstarter_pb2_grpc.ExporterServiceStub(channel).GetReport(empty_pb2.Empty())

    for report in response.reports:
        topo[report.uuid] = []

        if report.parent_uuid != "":
            topo[report.parent_uuid].append(report.uuid)

        reports[report.uuid] = report

    for uuid in TopologicalSorter(topo).static_order():
        report = reports[uuid]

        # reference: https://docs.djangoproject.com/en/5.0/_modules/django/utils/module_loading/#import_string
        module_path, class_name = report.labels["jumpstarter.dev/client"].rsplit(".", 1)
        client_class = getattr(import_module(module_path), class_name)
        client = client_class(
            uuid=UUID(uuid),
            labels=report.labels,
            channel=channel,
            portal=portal,
            children={reports[k].labels["jumpstarter.dev/name"]: clients[k] for k in topo[uuid]},
        )

        clients[uuid] = client

    return clients.popitem(last=True)[1]
