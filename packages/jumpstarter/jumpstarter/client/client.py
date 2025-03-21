from collections import OrderedDict, defaultdict
from contextlib import ExitStack, asynccontextmanager
from graphlib import TopologicalSorter
from uuid import UUID

import grpc
from anyio.from_thread import BlockingPortal
from google.protobuf import empty_pb2

from .grpc import SmartExporterStub
from jumpstarter.client import DriverClient
from jumpstarter.common.importlib import import_class
from jumpstarter.exporter.tls import SAN


@asynccontextmanager
async def client_from_path(
    path: str,
    portal: BlockingPortal,
    stack: ExitStack,
    allow: list[str],
    unsafe: bool,
    use_alternative_endpoints: bool = False,
):
    async with grpc.aio.secure_channel(
        f"unix://{path}", grpc.local_channel_credentials(grpc.LocalConnectionType.UDS)
    ) as channel:
        yield await client_from_channel(channel, portal, stack, allow, unsafe, use_alternative_endpoints)


async def client_from_channel(
    channel: grpc.aio.Channel,
    portal: BlockingPortal,
    stack: ExitStack,
    allow: list[str],
    unsafe: bool,
    use_alternative_endpoints: bool = False,
) -> DriverClient:
    topo = defaultdict(list)
    last_seen = {}
    reports = {}
    clients = OrderedDict()

    response = await SmartExporterStub([channel]).GetReport(empty_pb2.Empty())

    channels = [channel]
    if use_alternative_endpoints:
        for endpoint in response.alternative_endpoints:
            if endpoint.certificate:
                channels.append(
                    grpc.aio.secure_channel(
                        endpoint.endpoint,
                        grpc.ssl_channel_credentials(
                            root_certificates=endpoint.certificate.encode(),
                            private_key=endpoint.client_private_key.encode(),
                            certificate_chain=endpoint.client_certificate.encode(),
                        ),
                        options=(("grpc.ssl_target_name_override", SAN),),
                    )
                )

    stub = SmartExporterStub(list(reversed(channels)))

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
