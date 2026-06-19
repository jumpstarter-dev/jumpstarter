import logging
import os
from collections import OrderedDict, defaultdict
from contextlib import ExitStack, asynccontextmanager
from graphlib import TopologicalSorter
from uuid import UUID

from anyio.from_thread import BlockingPortal

from jumpstarter.client import DriverClient
from jumpstarter.client.base import StubDriverClient
from jumpstarter.common.exceptions import MissingDriverError
from jumpstarter.common.importlib import import_class
from jumpstarter.config.tls import TLSConfigV1Alpha1

logger = logging.getLogger(__name__)


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
    """Create a DriverClient connected to a leased exporter over the local transport socket.

    The in-lease driver client routes through the Rust core (FFI, ``jumpstarter_core``) — no
    Python gRPC. ``path`` is the ``JUMPSTARTER_HOST`` Unix socket served by the lease transport.
    The ``tls_config``/``grpc_options``/``insecure``/``passphrase`` keywords are accepted for
    call-site compatibility but unused on the FFI path (TLS/auth to the controller live in the
    Rust core).
    """
    yield await client_from_host(str(path), portal, stack, allow, unsafe)


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
    import jumpstarter_core as jc

    session = await jc.ClientSession.connect(str(host))
    return await client_from_session(session, portal, stack, allow, unsafe)


async def client_from_session(
    session,
    portal: BlockingPortal,
    stack: ExitStack,
    allow: list[str],
    unsafe: bool,
) -> DriverClient:
    """Build a DriverClient tree from any object presenting the ClientSession interface
    (``get_report``/``driver_call``/``streaming_driver_call``/``stream``/…). The network path
    passes a Rust ``jumpstarter_core.ClientSession``; ``serve()`` passes a pure-Python
    ``LocalSession`` over an in-process driver host — same driver-client code, no transport."""
    import json

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
