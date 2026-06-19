"""Client-side lease handling, backed by the Rust core (jumpstarter_core.ControllerSession).

The controller/lease protocol (acquire FSM, controller gRPC, the local transport listener
that bridges to the leased exporter) lives entirely in the Rust core. This module is a thin
Python shim presenting the historic ``Lease`` API (``request``/``serve_unix``/``connect``/
``monitor`` + the ``with``/``async with`` lifecycle) over that FFI surface, so callers like
``jumpstarter-testing`` and the MCP server keep working without any Python gRPC.
"""

import logging
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Self

from anyio import (
    AsyncContextManagerMixin,
    CancelScope,
    ContextManagerMixin,
    create_task_group,
    fail_after,
    sleep,
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
    lease_ending_callback: Callable[[Self, timedelta], None] | None = field(default=None, init=False)
    lease_ended: bool = field(default=False, init=False)
    lease_transferred: bool = field(default=False, init=False)
    _session: Any = field(default=None, init=False)
    _acquired_at: datetime | None = field(default=None, init=False)

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
        self._acquired_at = datetime.now(timezone.utc)
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

    def _notify_lease_ending(self, remaining: timedelta) -> None:
        if remaining <= timedelta(0):
            self.lease_ended = True
            logger.info("Lease %s ended", self.name)
        if self.lease_ending_callback is not None:
            self.lease_ending_callback(self, remaining)

    @asynccontextmanager
    async def monitor_async(self, threshold: timedelta = timedelta(minutes=5)):
        """Best-effort lease-expiry notification based on the acquired duration.

        The controller enforces the lease lifetime; this fires ``lease_ending_callback`` once
        the remaining time crosses ``threshold`` and again at expiry. (It does not poll the
        controller for externally-shortened/transferred leases — a degradation from the gRPC
        path, acceptable for the FFI shim.)
        """

        async def _monitor():
            if self._acquired_at is None:
                return
            end_time = self._acquired_at + self.duration
            notified = False
            while True:
                remaining = end_time - datetime.now(timezone.utc)
                if remaining <= timedelta(0):
                    self._notify_lease_ending(timedelta(0))
                    break
                if not notified and remaining <= threshold:
                    notified = True
                    self._notify_lease_ending(remaining)
                await sleep(min(remaining.total_seconds(), 30))

        async with create_task_group() as tg:
            tg.start_soon(_monitor)
            try:
                yield
            finally:
                tg.cancel_scope.cancel()

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

    @contextmanager
    def monitor(self, threshold: timedelta = timedelta(minutes=5)):
        with self.portal.wrap_async_context_manager(self.monitor_async(threshold)):
            yield
