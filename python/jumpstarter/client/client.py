from collections import OrderedDict, defaultdict
from contextlib import asynccontextmanager
from graphlib import TopologicalSorter
from uuid import UUID

import grpc
from google.protobuf import empty_pb2

from jumpstarter.client import DriverClient
from jumpstarter.common.importlib import import_class
from jumpstarter.v1 import (
    jumpstarter_pb2_grpc,
)


@asynccontextmanager
async def client_from_path(path, portal):
    async with grpc.aio.secure_channel(
        f"unix://{path}", grpc.local_channel_credentials(grpc.LocalConnectionType.UDS)
    ) as channel:
        yield await client_from_channel(channel, portal)


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

        client_class = import_class(report.labels["jumpstarter.dev/client"], [], True)  # FIXME: set allowlist
        client = client_class(
            uuid=UUID(uuid),
            labels=report.labels,
            channel=channel,
            portal=portal,
            children={reports[k].labels["jumpstarter.dev/name"]: clients[k] for k in topo[uuid]},
        )

        clients[uuid] = client

    return clients.popitem(last=True)[1]
