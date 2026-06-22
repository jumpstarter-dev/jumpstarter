"""Client-side lease handling, backed by the Rust core (jumpstarter_core.ControllerSession).

The controller/lease protocol (acquire FSM, controller gRPC, the local transport listener
that bridges to the leased exporter) lives entirely in the Rust core. This module is a thin
Python shim presenting the ``Lease`` API (``acquire``/``request``/``serve_unix``/``connect``
+ the ``with``/``async with`` lifecycle) over that FFI surface, so callers like
``jumpstarter-testing`` keep working without any Python gRPC.
"""

import logging
from collections.abc import AsyncGenerator, Generator
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Self

from anyio import (
    AsyncContextManagerMixin,
    CancelScope,
    ContextManagerMixin,
    fail_after,
)
from anyio.from_thread import BlockingPortal

from .exceptions import LeaseError
from jumpstarter.client import client_from_path

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class Lease(ContextManagerMixin, AsyncContextManagerMixin):
    # Controller connection (resolved from the client config by config.lease_async).
    endpoint: str
    namespace: str
    token: str | None = None
    ca: str = ""
    insecure: bool = False
    client_name: str | None = None

    # Lease parameters.
    duration: timedelta
    selector: str | None = None
    requested_exporter_name: str | None = None
    name: str | None = field(default=None)
    tags: dict[str, str] = field(default_factory=dict)
    allow: list[str]
    unsafe: bool
    release: bool = True  # release on context exit
    portal: BlockingPortal
    acquisition_timeout: int = field(default=7200)  # seconds

    # Runtime state.
    exporter_name: str = field(default="remote", init=False)
    _session: Any = field(default=None, init=False)

    async def _ensure_session(self):
        if self._session is None:
            import jumpstarter_core as jc

            self._session = await jc.ControllerSession.connect(
                self.endpoint,
                self.token,
                self.ca,
                self.insecure,
                self.namespace,
                self.client_name or "",
            )
        return self._session

    async def request_async(self) -> Self:
        import jumpstarter_core as jc

        session = await self._ensure_session()
        try:
            acquired = await session.acquire_lease(
                self.selector,
                self.requested_exporter_name,
                self.name or None,
                int(self.duration.total_seconds()),
                int(self.acquisition_timeout),
            )
        except jc.ControllerError as e:
            raise LeaseError(f"acquiring lease: {e}") from None
        self.name = acquired.name
        self.exporter_name = acquired.exporter
        logger.info("Acquired Lease %s on exporter %s", self.name, self.exporter_name)
        return self

    def request(self) -> Self:
        return self.portal.call(self.request_async)

    @asynccontextmanager
    async def serve_unix_async(self):
        session = await self._ensure_session()
        transport = await session.serve_lease(self.name)
        try:
            yield await transport.jumpstarter_host()
        finally:
            await transport.close()

    @asynccontextmanager
    async def connect_async(self, stack):
        async with self.serve_unix_async() as path:
            async with client_from_path(path, self.portal, stack, allow=self.allow, unsafe=self.unsafe) as client:
                yield client

    @asynccontextmanager
    async def __asynccontextmanager__(self) -> AsyncGenerator[Self]:
        try:
            yield await self.request_async()
        finally:
            if self.release and self.name and self._session is not None:
                # Shield cleanup from cancellation to ensure release completes.
                with CancelScope(shield=True):
                    try:
                        with fail_after(30):
                            logger.info("Releasing Lease %s", self.name)
                            await self._session.release_lease(self.name)
                    except TimeoutError:
                        logger.warning("Timeout while releasing lease %s during cleanup", self.name)
                    except Exception:
                        logger.debug("Error during lease cleanup for %s (likely already released)", self.name)

    @contextmanager
    def __contextmanager__(self) -> Generator[Self]:
        with self.portal.wrap_async_context_manager(self) as value:
            yield value

    @contextmanager
    def connect(self):
        with ExitStack() as stack:
            with self.portal.wrap_async_context_manager(self.connect_async(stack)) as client:
                yield client

    @contextmanager
    def serve_unix(self):
        with self.portal.wrap_async_context_manager(self.serve_unix_async()) as path:
            yield path

    @classmethod
    @contextmanager
    def acquire(
        cls,
        *,
        alias: str = "default",
        selector: str | None = None,
        exporter_name: str | None = None,
        lease_name: str | None = None,
        duration: timedelta = timedelta(minutes=30),
    ) -> Generator[Self]:
        """Resolve a client config by alias and acquire a lease (blocking).

        The connection fields are parsed by the Rust core (``jc.load_client_connection``,
        which owns config YAML); this manages the blocking portal + lease lifecycle. Replaces
        the former ``jumpstarter.client.config.ClientConnection.load(alias).lease(...)``; used
        by ``jumpstarter-testing`` when not already inside a ``jmp shell``.
        """
        import os
        from pathlib import Path

        import jumpstarter_core as jc
        from anyio.from_thread import start_blocking_portal

        from jumpstarter.common.xdg import xdg_config_home
        from jumpstarter.config.env import JMP_CLIENT_CONFIG_HOME, JMP_LEASE

        home = Path(os.getenv(JMP_CLIENT_CONFIG_HOME) or (xdg_config_home() / "jumpstarter"))
        spec = jc.load_client_connection(str(home / "clients" / f"{alias}.yaml"))
        # Preserve the legacy ``"UNSAFE" in allow`` sentinel (the Rust spec carries the raw flag).
        unsafe = spec.unsafe or "UNSAFE" in spec.allow
        lease_name = lease_name or os.environ.get(JMP_LEASE, "")
        with start_blocking_portal() as portal:
            with cls(
                endpoint=spec.endpoint,
                namespace=spec.namespace or "default",
                token=spec.token,
                ca=spec.ca,
                insecure=spec.insecure,
                client_name=spec.name,
                duration=duration,
                selector=selector,
                requested_exporter_name=exporter_name,
                name=lease_name,
                allow=spec.allow,
                unsafe=unsafe,
                release=(lease_name == ""),
                portal=portal,
                acquisition_timeout=spec.acquisition_timeout,
            ) as lease:
                yield lease
