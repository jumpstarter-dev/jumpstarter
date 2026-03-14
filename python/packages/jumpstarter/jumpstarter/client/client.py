import logging
import os
from collections import OrderedDict, defaultdict
from contextlib import ExitStack, asynccontextmanager
from graphlib import TopologicalSorter
from uuid import UUID

import grpc
from anyio.from_thread import BlockingPortal
from google.protobuf import empty_pb2

from .grpc import MultipathExporterStub
from jumpstarter.client import DriverClient
from jumpstarter.client.base import StubDriverClient
from jumpstarter.common.exceptions import MissingDriverError
from jumpstarter.common.grpc import aio_secure_channel, ssl_channel_credentials
from jumpstarter.common.importlib import import_class
from jumpstarter.config.tls import TLSConfigV1Alpha1

logger = logging.getLogger(__name__)


def _is_tcp_address(path: str) -> bool:
    """Return True if path looks like host:port (TCP address)."""
    if ":" not in path:
        return False
    parts = path.rsplit(":", 1)
    if len(parts) != 2:
        return False
    try:
        port = int(parts[1], 10)
        return 1 <= port <= 65535
    except ValueError:
        return False


@asynccontextmanager
async def client_from_path(
    path: str,
    portal: BlockingPortal,
    stack: ExitStack,
    allow: list[str],
    unsafe: bool,
    *,
    tls_config: TLSConfigV1Alpha1 | None = None,
    grpc_options: dict | None = None,
    insecure: bool = False,
):
    """Create a DriverClient from a Unix socket path or a TCP address (host:port).

    When path is a TCP address (e.g. exporter.host.name:1234), use tls_config and
    insecure to build the channel. When path is a Unix path, those are ignored.
    """
    path = str(path)
    if _is_tcp_address(path):
        if insecure:
            async with grpc.aio.insecure_channel(path) as channel:
                yield await client_from_channel(channel, portal, stack, allow, unsafe)
        else:
            tls = tls_config or TLSConfigV1Alpha1()
            credentials = await ssl_channel_credentials(path, tls)
            async with aio_secure_channel(path, credentials, grpc_options) as channel:
                yield await client_from_channel(channel, portal, stack, allow, unsafe)
    else:
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

        try:
            client_class = import_class(report.labels["jumpstarter.dev/client"], allow, unsafe)
        except MissingDriverError as e:
            # Create stub client instead of failing
            # Suppress duplicate warnings
            if not os.environ.get("_JMP_SUPPRESS_DRIVER_WARNINGS"):
                logger.warning("Driver client '%s' is not available.", e.class_path)
            client_class = StubDriverClient

        client = client_class(
            uuid=UUID(report.uuid),
            labels=report.labels,
            stub=stub,
            portal=portal,
            stack=stack.enter_context(ExitStack()),
            children={reports[k].labels["jumpstarter.dev/name"]: clients[k] for k in topo[index]},
            description=getattr(report, "description", None) or None,
            methods_description=getattr(report, "methods_description", {}) or {},
        )

        clients[index] = client

    return clients.popitem(last=True)[1]
