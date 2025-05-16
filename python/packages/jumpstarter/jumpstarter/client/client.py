from collections import OrderedDict, defaultdict
from contextlib import ExitStack, asynccontextmanager
from graphlib import TopologicalSorter
from uuid import UUID

import grpc
from anyio.from_thread import BlockingPortal
from google.protobuf import empty_pb2

from .grpc import MultipathExporterStub
from jumpstarter.client import DriverClient
from jumpstarter.common.importlib import import_class


@asynccontextmanager
async def client_from_path(path: str, portal: BlockingPortal, stack: ExitStack, allow: list[str], unsafe: bool):
    async with grpc.aio.secure_channel(
        f"unix://{path}", grpc.local_channel_credentials(grpc.LocalConnectionType.UDS)
    ) as channel:
        yield await client_from_channel(channel, portal, stack, allow, unsafe)


async def client_from_channel(
    channel: grpc.aio.Channel,
    portal: BlockingPortal,
    stack: ExitStack,
    allow: list[str],
    unsafe: bool,
) -> DriverClient:
    topo = defaultdict(list)
    last_seen = {}
    reports = {}
    clients = OrderedDict()

    stub = MultipathExporterStub([channel])

    response = await stub.GetReport(empty_pb2.Empty())

    for index, report in enumerate(response.reports):
        topo[index] = []

        last_seen[report.uuid] = index

        if report.parent_uuid != "":
            parent_index = last_seen[report.parent_uuid]
            topo[parent_index].append(index)

        reports[index] = report

    for index in TopologicalSorter(topo).static_order():
        report = reports[index]

        client_class = import_class(report.labels["jumpstarter.dev/client"], allow, unsafe)
        client = client_class(
            uuid=UUID(report.uuid),
            labels=report.labels,
            stub=stub,
            portal=portal,
            stack=stack.enter_context(ExitStack()),
            children={reports[k].labels["jumpstarter.dev/name"]: clients[k] for k in topo[index]},
        )

        clients[index] = client

    return clients.popitem(last=True)[1]
