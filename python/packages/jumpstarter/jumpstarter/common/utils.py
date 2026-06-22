from contextlib import ExitStack, asynccontextmanager, contextmanager
from typing import TYPE_CHECKING

from anyio.from_thread import BlockingPortal, start_blocking_portal

from jumpstarter.utils.env import env

if TYPE_CHECKING:
    from jumpstarter.driver import Driver

__all__ = ["env", "lease", "serve"]


@asynccontextmanager
async def lease_async(
    portal: BlockingPortal,
    stack: ExitStack,
    *,
    selector: str | None = None,
    alias: str = "default",
    duration_secs: int = 1800,
):
    """Auto-acquire a lease for a client config and connect a client.

    The lease lifecycle (resolve config, acquire, serve on a local socket, release) lives in
    the Rust core (``jumpstarter_core.LeasedExporter`` — the auto-acquire capability shared by
    every language runtime); this binds it to the Python client tree. The standalone
    counterpart to :func:`env` (which connects to an already-leased exporter via
    ``JUMPSTARTER_HOST``), used by ``jumpstarter-testing`` when not inside a ``jmp shell``.
    """
    import os
    from pathlib import Path

    import jumpstarter_core as jc

    from jumpstarter.client import client_from_path
    from jumpstarter.common.xdg import xdg_config_home
    from jumpstarter.config.env import JMP_CLIENT_CONFIG_HOME

    home = Path(os.getenv(JMP_CLIENT_CONFIG_HOME) or (xdg_config_home() / "jumpstarter"))
    exporter = await jc.LeasedExporter.acquire(
        str(home / "clients" / f"{alias}.yaml"), selector, None, None, duration_secs
    )
    try:
        async with client_from_path(
            exporter.jumpstarter_host(),
            portal,
            stack,
            allow=exporter.allow(),
            unsafe=exporter.unsafe_drivers(),
        ) as client:
            yield client
    finally:
        await exporter.release()


@contextmanager
def lease(*, selector: str | None = None, alias: str = "default", duration_secs: int = 1800):
    """Blocking auto-acquire: yield a client connected to a freshly-leased exporter."""
    with start_blocking_portal() as portal:
        with ExitStack() as stack:
            with portal.wrap_async_context_manager(
                lease_async(portal, stack, selector=selector, alias=alias, duration_secs=duration_secs)
            ) as client:
                yield client


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
