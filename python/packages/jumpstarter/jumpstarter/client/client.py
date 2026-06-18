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
from jumpstarter.common.grpc import _override_default_grpc_options, aio_secure_channel, ssl_channel_credentials
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
    passphrase: str | None = None,
):
    """Create a DriverClient from a Unix socket path or a TCP address (host:port).

    When path is a TCP address (e.g. exporter.host.name:1234), use tls_config and
    insecure to build the channel. When path is a Unix path, those are ignored.
    passphrase, if set, is injected as metadata on every RPC via client interceptors.
    """
    interceptors = None
    if passphrase:
        from jumpstarter.exporter.auth import passphrase_client_interceptors

        interceptors = passphrase_client_interceptors(passphrase)

    path = str(path)
    if _is_tcp_address(path):
        if insecure:
            async with grpc.aio.insecure_channel(
                path,
                options=_override_default_grpc_options(grpc_options),
                interceptors=interceptors,
            ) as channel:
                yield await client_from_channel(channel, portal, stack, allow, unsafe)
        else:
            tls = tls_config or TLSConfigV1Alpha1()
            credentials = await ssl_channel_credentials(path, tls)
            async with aio_secure_channel(
                path, credentials, grpc_options, interceptors=interceptors
            ) as channel:
                yield await client_from_channel(channel, portal, stack, allow, unsafe)
    elif os.environ.get("JMP_CLIENT_FFI"):
        # Opt-in in-process client path: route driver calls through the Rust core (FFI)
        # over the local transport socket, instead of grpcio. (Default stays gRPC until
        # the FFI client covers byte streams / resources / logs.)
        yield await client_from_host(path, portal, stack, allow, unsafe)
    else:
        async with grpc.aio.secure_channel(
            f"unix://{path}",
            grpc.local_channel_credentials(grpc.LocalConnectionType.UDS),
            # grpcio defaults :authority to the percent-encoded socket path, which a
            # strict HTTP/2 server (the Rust exporter core) rejects as a malformed
            # authority. Pin a valid authority; grpcio servers accept it unchanged.
            options=[("grpc.default_authority", "localhost")],
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


async def client_from_host(
    host: str,
    portal: BlockingPortal,
    stack: ExitStack,
    allow: list[str],
    unsafe: bool,
) -> DriverClient:
    """Build a DriverClient tree over the Rust core (FFI, jumpstarter_core.ClientSession)
    instead of a gRPC channel — the in-process client path. Driver calls route through the
    Rust core; no grpcio / generated stubs."""
    import json

    import jumpstarter_core as jc

    session = await jc.ClientSession.connect(str(host))
    reports = json.loads(await session.get_report())

    topo = defaultdict(list)
    last_seen = {}
    by_index = {}
    clients = OrderedDict()

    for index, report in enumerate(reports):
        topo[index] = []
        last_seen[report["uuid"]] = index
        parent = report.get("parent_uuid")
        if parent:
            topo[last_seen[parent]].append(index)
        by_index[index] = report

    for index in TopologicalSorter(topo).static_order():
        report = by_index[index]
        try:
            client_class = import_class(report["labels"]["jumpstarter.dev/client"], allow, unsafe)
        except MissingDriverError as e:
            if not os.environ.get("_JMP_SUPPRESS_DRIVER_WARNINGS"):
                logger.warning("Driver client '%s' is not available.", e.class_path)
            client_class = StubDriverClient

        client = client_class(
            uuid=UUID(report["uuid"]),
            labels=report["labels"],
            session=session,
            portal=portal,
            stack=stack.enter_context(ExitStack()),
            children={by_index[k]["labels"]["jumpstarter.dev/name"]: clients[k] for k in topo[index]},
            description=report.get("description") or None,
            methods_description=report.get("methods_description") or {},
        )
        clients[index] = client

    return clients.popitem(last=True)[1]
