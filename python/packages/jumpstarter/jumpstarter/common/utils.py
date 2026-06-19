from contextlib import ExitStack, asynccontextmanager, contextmanager
from typing import TYPE_CHECKING

from anyio.from_thread import BlockingPortal, start_blocking_portal

from jumpstarter.utils.env import env

if TYPE_CHECKING:
    from jumpstarter.driver import Driver

__all__ = ["env"]


@asynccontextmanager
async def serve_async(root_device: "Driver", portal: BlockingPortal, stack: ExitStack):
    """Serve a locally-constructed driver tree to an in-process client.

    Both ends live in this process, so there is no transport: a ``DriverHost`` adapter wraps
    the driver tree and a pure-Python ``LocalSession`` presents it through the same
    ClientSession interface the Rust core exposes. Driver tests therefore exercise the exact
    FFI-shaped dispatch (introspection, JSON value codec, handle-based streams) used by the
    real exporter — without grpc, a Unix socket, or the old Python ``Session``.
    """
    from jumpstarter.client.client import client_from_session
    from jumpstarter.exporter.host import DriverHost, LocalSession

    # SAFETY: the root_device instance is constructed locally thus considered trusted
    host = DriverHost(root_device)
    session = LocalSession(host)
    client = await client_from_session(session, portal, stack, allow=[], unsafe=True)
    try:
        yield client
    finally:
        if hasattr(client, "close"):
            client.close()


@contextmanager
def serve(root_device: "Driver"):
    with start_blocking_portal() as portal:
        with ExitStack() as stack:
            with portal.wrap_async_context_manager(serve_async(root_device, portal, stack)) as client:
                try:
                    yield client
                finally:
                    if hasattr(client, "close"):
                        client.close()
