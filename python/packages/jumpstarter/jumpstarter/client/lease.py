import inspect
import logging
import os
import sys
import time
import types
from collections.abc import AsyncGenerator, Callable, Generator, Iterable, Mapping
from contextlib import (
    AsyncExitStack,
    ExitStack,
    asynccontextmanager,
    contextmanager,
)
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, ClassVar, Self, Union, get_args, get_origin

import grpc
from anyio import (
    AsyncContextManagerMixin,
    CancelScope,
    ContextManagerMixin,
    create_task_group,
    fail_after,
    sleep,
)
from anyio.from_thread import BlockingPortal
from grpc.aio import AioRpcError, Channel
from jumpstarter_protocol import jumpstarter_pb2, jumpstarter_pb2_grpc
from rich.console import Console
from tenacity import retry, retry_if_exception_type, wait_exponential_jitter
from xdg_base_dirs import xdg_runtime_dir

from .base import DriverClient
from .exceptions import LeaseError, LeaseMemberLostError
from jumpstarter.client import client_from_path
from jumpstarter.client.grpc import ClientService, LeaseMemberStatus
from jumpstarter.client.grpc import LeaseConstraint as LeaseConstraintModel
from jumpstarter.client.grpc import LeaseMember as LeaseMemberModel
from jumpstarter.common import TemporaryUnixListener
from jumpstarter.common.condition import condition_false, condition_message, condition_present_and_equal, condition_true
from jumpstarter.common.exceptions import ConnectionError, ExporterUnreachableError
from jumpstarter.common.grpc import is_controller_unavailable, translate_grpc_exceptions
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.config.tls import TLSConfigV1Alpha1

if TYPE_CHECKING:
    from jumpstarter.utils.env import ExporterMetadata

logger = logging.getLogger(__name__)

# Allow the controller's 30-second exporter readiness wait to finish.
_DIAL_ATTEMPT_TIMEOUT = 35.0
# Per call, so releasing a lease takes at most twice this.
_RELEASE_RPC_TIMEOUT_SECONDS = 15.0


def _deadline_exceeded(exc: BaseException) -> bool:
    """Whether *exc* is a gRPC call that ran out of its deadline, as translated by translate_grpc_exceptions."""
    cause = exc.__cause__
    return isinstance(cause, AioRpcError) and cause.code() == grpc.StatusCode.DEADLINE_EXCEEDED


@dataclass(kw_only=True)
class DirectLease:
    """Lease-like object for direct connection to an exporter (no controller).

    Used when connecting via jmp shell --tls-grpc host:port. Yields the address
    from serve_unix_async() so the client can connect directly; no Dial, no listener.
    """

    address: str  # host:port
    portal: BlockingPortal
    allow: list[str]
    unsafe: bool
    tls_config: TLSConfigV1Alpha1 = field(default_factory=TLSConfigV1Alpha1)
    grpc_options: dict[str, Any] = field(default_factory=dict)
    insecure: bool = False
    passphrase: str | None = None

    name: str = field(default="direct", init=False)
    exporter_name: str = field(default="direct", init=False)
    exporter_labels: dict[str, str] = field(default_factory=dict, init=False)
    release: bool = field(default=False, init=False)
    lease_ended: bool = field(default=False, init=False)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    @asynccontextmanager
    async def serve_unix_async(self):
        """Yield the TCP address (no listener); client connects directly."""
        yield self.address

    @asynccontextmanager
    async def monitor_async(self, threshold: timedelta = timedelta(minutes=5)):
        """No-op monitor for direct connection (no lease to monitor)."""
        yield


@dataclass(kw_only=True)
class Lease(ContextManagerMixin, AsyncContextManagerMixin):
    channel: Channel
    duration: timedelta | None
    selector: str | None
    requested_exporter_name: str | None = None
    portal: BlockingPortal
    namespace: str
    name: str | None = field(default=None)
    tags: dict[str, str] = field(default_factory=dict)
    allow: list[str]
    unsafe: bool
    release: bool = True  # release on contexts exit
    controller: jumpstarter_pb2_grpc.ControllerServiceStub = field(init=False)
    tls_config: TLSConfigV1Alpha1 = field(default_factory=TLSConfigV1Alpha1)
    grpc_options: dict[str, Any] = field(default_factory=dict)
    client_name: str | None = None  # Name of the current client, used for ownership validation
    allow_disabled: bool = False  # Allow leasing a disabled exporter (works with exporter_name and selector)
    acquisition_timeout: int = field(default=7200)  # Timeout in seconds for lease acquisition, polled in 5s intervals
    dial_timeout: float = field(default=60.0)  # Timeout in seconds for Dial retry loop when exporter not ready
    retry_timeout: float = field(default=300.0)  # Retry timeout for unreachable exporter (0 to disable)
    exporter_name: str = field(default="remote", init=False)  # Populated during acquisition
    exporter_labels: dict[str, str] = field(default_factory=dict, init=False)  # Populated during acquisition
    lease_ending_callback: Callable[[Self, timedelta], None] | None = field(
        default=None, init=False
    )  # Called when lease is ending
    lease_ended: bool = field(default=False, init=False)  # Set when lease expires naturally
    lease_transferred: bool = field(default=False, init=False)  # Set when lease is transferred to another client
    lease_revoked: bool = field(default=False, init=False)  # Set when this client loses access to the lease
    owner: str | None = field(default=None, init=False)  # The lease's owner when it was acquired

    def __post_init__(self):
        self.controller = jumpstarter_pb2_grpc.ControllerServiceStub(self.channel)
        self.svc = ClientService(channel=self.channel, namespace=self.namespace)

    def refresh_channel(self, channel: Channel):
        """Update the gRPC channel used for controller communication.

        This is used when the auth token is refreshed to update the
        underlying gRPC channel with new credentials.
        """
        self.channel = channel
        self.controller = jumpstarter_pb2_grpc.ControllerServiceStub(channel)
        self.svc = ClientService(channel=channel, namespace=self.namespace)

    async def _create(self):
        logger.debug("Creating lease request for selector %s for duration %s", self.selector, self.duration)
        with translate_grpc_exceptions():
            lease = await self.svc.CreateLease(
                selector=self.selector,
                exporter_name=self.requested_exporter_name,
                duration=self.duration,
                lease_id=self.name,
                tags=self.tags or None,
                allow_disabled=self.allow_disabled,
            )
            self.name = lease.name
            for label_key, message in lease.deprecated_labels.items():
                warning = f"selector label '{label_key}' is deprecated"
                if message:
                    warning += f": {message}"
                logger.warning(warning)
        logger.info("Acquiring lease %s for selector %s for duration %s", self.name, self.selector, self.duration)

    async def get(self, *, timeout: float | None = None):
        """Fetch the lease from the controller.

        timeout is a gRPC deadline. Prefer it to an anyio timeout around the
        call: a cancelled grpc.aio call can raise a CancelledError that anyio
        does not recognize as its own, which cancels the caller's task group
        instead of timing out.
        """
        with translate_grpc_exceptions():
            svc = ClientService(channel=self.channel, namespace=self.namespace)
            assert self.name is not None  # pragma: no cover
            return await svc.GetLease(name=self.name, timeout=timeout)

    @retry(
        wait=wait_exponential_jitter(initial=1, max=120, jitter=1),
        retry=retry_if_exception_type(ConnectionError),
        reraise=True,
    )
    async def _get_with_retry(self):
        """Get lease with exponential backoff retry on ConnectionError.

        Retries with exponential backoff and jitter indefinitely when ConnectionError occurs.
        The wait time between retries is capped at 2 minutes (120 seconds).
        Jitter helps prevent thundering herd problems when multiple clients retry simultaneously.
        """
        try:
            return await self.get()
        except ConnectionError as e:
            logger.error("Error while getting lease %s: %s", self.name, e)
            raise

    def request(self):
        """Request a lease, or verifies a lease which was already created.

        :return: lease
        :rtype: Lease
        :raises LeaseError: if lease is unsatisfiable
        :raises LeaseError: if lease is not pending
        :raises TimeoutError: if lease is not ready after timeout
        """
        return self.portal.call(self.request_async)

    async def request_async(self):
        """Request a lease, or verifies a lease which was already created.

        :return: lease
        :rtype: Lease
        :raises LeaseError: if lease is unsatisfiable
        :raises LeaseError: if lease is not pending
        :raises TimeoutError: if lease is not ready after timeout
        """
        if self.name:
            logger.debug("using existing lease via env or flag %s", self.name)
            existing_lease = await self.get()
            self._verify_existing_lease(existing_lease)
            if self.selector is not None and existing_lease.selector != self.selector:
                logger.warning(
                    "Existing lease from env or flag %s has selector '%s' but requested selector is '%s'. "
                    "Creating a new lease instead",
                    self.name,
                    existing_lease.selector,
                    self.selector,
                )
                self.name = None
                await self._create()
        else:
            await self._create()

        return await self._acquire()

    def _verify_existing_lease(self, existing_lease):
        if existing_lease.effective_end_time:
            raise LeaseError(f"lease {self.name} has already ended")
        if (
            self.client_name
            and existing_lease.client != self.client_name
            and not existing_lease.is_accessible_by(self.client_name)
        ):
            raise LeaseError(
                f"lease {self.name} belongs to client '{existing_lease.client}', "
                f"not the current client '{self.client_name}'"
            )

    async def _fetch_exporter_labels(self):
        """Fetch the exporter's labels after lease acquisition."""
        try:
            exporter = await self.svc.GetExporter(name=self.exporter_name)
            self.exporter_labels = exporter.labels
        except Exception as e:  # noqa: BLE001
            self.exporter_labels = {}
            logger.warning("Could not fetch labels for exporter %s: %s", self.exporter_name, e)

    def _update_spinner_status(self, spinner, result):
        """Update spinner with appropriate status message based on lease conditions."""
        if condition_true(result.conditions, "Pending"):
            pending_message = condition_message(result.conditions, "Pending")
            if pending_message:
                spinner.update_status(f"Waiting for lease: {pending_message}")
            else:
                spinner.update_status("Waiting for lease to be ready...")
        else:
            spinner.update_status("Waiting for server to provide status updates...")

    async def _handle_no_exporter_retry(self, spinner, message):
        """Handle transient NoExporter condition by retrying with a new lease.

        Old controllers (pre-918d6341) mark offline-but-matching exporters as
        Unsatisfiable with reason "NoExporter". This is transient, so we update
        the spinner, wait briefly, and create a new lease.
        """
        spinner.update_status(f"Waiting for lease: {message}")
        for _ in range(5):
            await sleep(1)
            spinner.tick()
        await self._create()

    async def _acquire(self):
        """Acquire a lease.

        Makes sure the lease is ready, and returns the lease object.
        """
        try:
            with fail_after(self.acquisition_timeout):
                with LeaseAcquisitionSpinner(self.name) as spinner:
                    while True:
                        logger.debug("Polling Lease %s", self.name)
                        result = await self._get_with_retry()
                        # A lease with members never becomes one exporter. Say so now:
                        # a degraded one is not Ready, and would give a misleading state error.
                        _reject_member_form(self.name, result)
                        # lease ready
                        if condition_true(result.conditions, "Ready"):
                            logger.debug("Lease %s acquired", self.name)
                            spinner.update_status(f"Lease {self.name} acquired successfully!", force=True)
                            self.exporter_name = result.exporter
                            self.owner = result.client
                            await self._fetch_exporter_labels()
                            break

                        # lease unsatisfiable
                        if condition_true(result.conditions, "Unsatisfiable"):
                            message = condition_message(result.conditions, "Unsatisfiable")
                            # Old controllers (pre-918d6341) mark offline-but-matching
                            # exporters as Unsatisfiable with reason "NoExporter".
                            # This is transient - retry with a new lease.
                            if condition_present_and_equal(result.conditions, "Unsatisfiable", "True", "NoExporter"):
                                await self._handle_no_exporter_retry(spinner, message)
                                continue
                            logger.debug("Lease %s cannot be satisfied: %s", self.name, message)
                            raise LeaseError(f"the lease cannot be satisfied: {message}")

                        # lease invalid
                        if condition_true(result.conditions, "Invalid"):
                            message = condition_message(result.conditions, "Invalid")
                            logger.debug("Lease %s is invalid: %s", self.name, message)
                            raise LeaseError(f"the lease is invalid: {message}")

                        # lease not pending
                        if condition_false(result.conditions, "Pending"):
                            raise LeaseError(
                                f"Lease {self.name} is not in pending, but it isn't in Ready or "
                                f"Unsatisfiable state either"
                            )

                        if condition_false(result.conditions, "Ready"):
                            message = condition_message(result.conditions, "Ready") or "lease is no longer active"
                            raise LeaseError(f"lease {self.name}: {message}")

                        # Update spinner with appropriate status message
                        self._update_spinner_status(spinner, result)

                        # Wait in 1-second increments with tick updates for better UX
                        for _ in range(5):
                            await sleep(1)
                            spinner.tick()
            return self

        except TimeoutError:
            logger.debug(f"Lease {self.name} acquisition timed out after {self.acquisition_timeout} seconds")
            raise LeaseError(
                f"lease {self.name} acquisition timed out after {self.acquisition_timeout} seconds"
            ) from None

    @asynccontextmanager
    async def __asynccontextmanager__(self) -> AsyncGenerator[Self]:
        try:
            value = await self.request_async()
            yield value
        finally:
            if self.release and self.name:
                # Shield cleanup from cancellation to ensure it completes. Each
                # call is bounded by a gRPC deadline; see get() for why not fail_after.
                with CancelScope(shield=True):
                    try:
                        # skip the message if the lease is already expired
                        lease = await self.get(timeout=_RELEASE_RPC_TIMEOUT_SECONDS)
                        if not lease.effective_end_time:
                            logger.info("Releasing Lease %s", self.name)
                        await self.svc.DeleteLease(name=self.name, timeout=_RELEASE_RPC_TIMEOUT_SECONDS)
                    except Exception as e:  # noqa: BLE001
                        if _deadline_exceeded(e):
                            logger.warning("Timeout while deleting lease %s during cleanup", self.name)
                        else:
                            logger.debug("Error during lease cleanup for %s (likely already expired)", self.name)

    @contextmanager
    def __contextmanager__(self) -> Generator[Self]:
        with self.portal.wrap_async_context_manager(self) as value:
            yield value

    async def _on_dial_failed_precondition(self, member_name: str | None) -> None:
        """Called when Dial answers FAILED_PRECONDITION, before the answer is read as "not ready yet"."""

    async def _dial_with_retry(self, member_name: str | None = None, exporter_name: str | None = None):
        """Dial the controller with exponential backoff, waiting for the exporter to be ready.

        member_name is the role to dial in a member-form lease.

        Returns DialResponse on success.
        Raises ExporterUnreachableError on timeout or unrecoverable error.
        """
        logger.debug("Dialing controller for lease %s", self.name)
        exporter_name = exporter_name or self.exporter_name
        delay = 0.3
        deadline = time.monotonic() + self.dial_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExporterUnreachableError(
                    f"Exporter {exporter_name} unreachable after {self.dial_timeout:.0f}s"
                )
            try:
                # Unary grpc.aio calls need timeout=; AnyIO timeouts can leak CancelledError.
                assert self.name is not None
                request = jumpstarter_pb2.DialRequest(lease_name=self.name)
                if member_name is not None:
                    request.member_name = member_name
                return await self.controller.Dial(
                    request,
                    timeout=min(_DIAL_ATTEMPT_TIMEOUT, remaining),
                )
            except AioRpcError as e:
                details = e.details() or ""
                if e.code() == grpc.StatusCode.FAILED_PRECONDITION:
                    await self._on_dial_failed_precondition(member_name)
                # These readiness responses come from
                # controller/internal/service/controller_service.go.
                # An initial "exporter is offline" Dial must fail so the shell
                # can release this lease and re-acquire another.
                exporter_recovering = e.code() == grpc.StatusCode.FAILED_PRECONDITION and "not ready" in details
                controller_unavailable = is_controller_unavailable(e)
                if not (controller_unavailable or exporter_recovering):
                    if "permission denied" in details.lower():
                        self.lease_transferred = True
                        raise ExporterUnreachableError(
                            f"Lease {self.name} transferred to another client"
                        ) from e
                    raise ExporterUnreachableError(
                        f"Connection to exporter {exporter_name} lost: {details}"
                    ) from e
                retry_reason = details
            remaining = deadline - time.monotonic()
            if remaining > 0:
                logger.log(
                    logging.WARNING if controller_unavailable else logging.DEBUG,
                    "%s, retrying Dial (%.1fs remaining)",
                    retry_reason,
                    remaining,
                )
                await sleep(min(delay, remaining))
                delay = min(delay * 2, 2.0)

    @asynccontextmanager
    async def serve_unix_async(self):
        async with self._serve_tunnel() as path:
            yield path

    @asynccontextmanager
    async def _serve_tunnel(
        self,
        member_name: str | None = None,
        exporter_name: str | None = None,
        path: os.PathLike | None = None,
    ):
        """Serve one exporter of the lease on a local Unix socket."""
        # Wait for exporter readiness before accepting connections.
        # The response is intentionally discarded — each connection needs
        # its own Dial to get a unique router tunnel.
        dial = self._dial_with_retry
        if member_name is not None:
            dial = partial(self._dial_with_retry, member_name, exporter_name)
        await dial()

        async def _tunnel_handler(stream):
            # A failed command tunnel closes only this socket. The listener
            # must stay up so other and later commands can keep working.
            try:
                response = await dial()
                async with connect_router_stream(
                    response.router_endpoint,
                    response.router_token,
                    stream,
                    self.tls_config,
                    self.grpc_options,
                ):
                    pass
            except Exception as e:  # noqa: BLE001
                logger.warning("Closing connection after tunnel failure: %s", e)
                await stream.aclose()

        async with TemporaryUnixListener(_tunnel_handler, path) as socket_path:
            logger.debug("Serving Unix socket at %s", socket_path)
            yield socket_path

    def _notify_lease_ending(self, remaining: timedelta) -> None:
        """Set lease_ended flag and invoke the ending callback if set."""
        if remaining <= timedelta(0):
            self.lease_ended = True
        if self.lease_ending_callback is not None:
            self.lease_ending_callback(self, remaining)

    def _get_lease_end_time(self, lease) -> datetime | None:
        """Extract the end time from a lease response, or None if not available."""
        if lease.effective_end_time:
            return lease.effective_end_time
        if not (lease.effective_begin_time and lease.duration):
            return None
        return lease.effective_begin_time + lease.duration

    @asynccontextmanager
    async def monitor_async(self, threshold: timedelta = timedelta(minutes=5)):
        async def _monitor():
            check_interval = 30  # seconds - check periodically for external lease changes
            last_known_end_time = None
            while True:
                try:
                    lease = await self.get()
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to check lease %s status: %s", self.name, e)
                    # If we know when the lease should end, use it to bound the sleep
                    if last_known_end_time is not None:
                        remain = (last_known_end_time - datetime.now(tz=UTC).astimezone()).total_seconds()
                        if remain <= 0:
                            logger.info(
                                "Lease %s estimated to have ended at %s (unable to confirm with server)",
                                self.name,
                                last_known_end_time,
                            )
                            self._notify_lease_ending(timedelta(0))
                            break
                        await sleep(min(check_interval, remain))
                    else:
                        await sleep(check_interval)
                    continue

                end_time = self._get_lease_end_time(lease)
                if end_time is None:
                    await sleep(1)
                    continue

                last_known_end_time = end_time
                remain = end_time - datetime.now(tz=UTC).astimezone()
                if remain < timedelta(0):
                    logger.info(f"Lease {self.name} ended at {end_time}")
                    self._notify_lease_ending(timedelta(0))
                    break

                # Log once when entering the threshold window
                if threshold - timedelta(seconds=check_interval) <= remain < threshold:
                    logger.info(
                        f"Lease {self.name} ending in {int((remain.total_seconds() + 30) // 60)} minutes at {end_time}"
                    )
                    self._notify_lease_ending(remain)
                await sleep(min(remain.total_seconds(), check_interval))

        async with create_task_group() as tg:
            tg.start_soon(_monitor)
            try:
                yield
            finally:
                tg.cancel_scope.cancel()

    @asynccontextmanager
    async def connect_async(self, stack):
        async with (
            self.serve_unix_async() as path,
            client_from_path(path, self.portal, stack, allow=self.allow, unsafe=self.unsafe) as client,
        ):
            yield client

    @contextmanager
    def connect(self):
        with ExitStack() as stack, self.portal.wrap_async_context_manager(self.connect_async(stack)) as client:
            yield client

    @contextmanager
    def serve_unix(self):
        with self.portal.wrap_async_context_manager(self.serve_unix_async()) as path:
            yield path

    @contextmanager
    def monitor(self, threshold: timedelta = timedelta(minutes=5)):
        with self.portal.wrap_async_context_manager(self.monitor_async(threshold)):
            yield


def _reject_member_form(name: str | None, lease) -> None:
    """A lease with members gives one client for each role, so it cannot be used as one exporter (DD-2)."""
    members = getattr(lease, "members", None)
    if isinstance(members, list) and members:
        roles = ", ".join(member.name for member in members)
        raise LeaseError(
            f"lease {name} has members ({roles}), not one exporter: "
            "request it with a LeaseMembers class, or call env_members() in its shell"
        )


_CLIENT_LABEL = "jumpstarter.dev/client"
_RESERVED_ATTRIBUTES = ("by_role", "metadata")
# The duration of a lease from a template that has no default, as for a lease of one exporter.
_DEFAULT_DURATION = timedelta(minutes=30)
# Longest path that fits sockaddr_un on every platform we run on (104 bytes on macOS).
_MAX_SOCKET_PATH = 100
# Codes that a call gets when its exporter is gone, as opposed to an error of the driver.
_CONNECTION_LOSS_CODES = frozenset(
    {
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.CANCELLED,
        grpc.StatusCode.INTERNAL,
        grpc.StatusCode.FAILED_PRECONDITION,
    }
)


@dataclass(frozen=True)
class _Member:
    role: str | None = None


def member(*, role: str | None = None) -> Any:
    """Name the role of a `LeaseMembers` attribute when it is not the attribute name.

    .. code-block:: python

        class Bench(LeaseMembers, template="bench"):
            records: Recorder = member(role="metadata")
    """
    return _Member(role=role)


@dataclass(frozen=True)
class _DriverSpec:
    client_class: type
    optional: bool
    drivers: "dict[str, _DriverSpec]"


@dataclass(frozen=True)
class _RoleSpec:
    role: str
    attribute: str
    client_class: type
    optional: bool
    drivers: dict[str, _DriverSpec]


def _composite_base(cls: Any) -> type | None:
    """The `CompositeClient` class that cls derives from, if any.

    It is found by name: the class lives in jumpstarter-driver-composite,
    which this package does not depend on.
    """
    if not isinstance(cls, type) or not issubclass(cls, DriverClient):
        return None
    for base in cls.__mro__:
        if base.__name__ == "CompositeClient":
            return base
    return None


def _split_optional(annotation: Any) -> tuple[Any, bool]:
    """Split `X | None` into (X, True). Any other annotation is returned as it is."""
    if get_origin(annotation) in (Union, types.UnionType):
        args = get_args(annotation)
        others = [arg for arg in args if arg is not type(None)]
        if len(others) == 1:
            return others[0], len(others) != len(args)
    return annotation, False


def _own_annotations(cls: type, localns: Mapping[str, Any]) -> dict[str, Any]:
    """The annotations that cls itself declares, with string annotations evaluated."""
    module = sys.modules.get(cls.__module__)
    globalns = dict(vars(module)) if module is not None else {}
    resolved = {}
    for name, annotation in inspect.get_annotations(cls).items():
        if isinstance(annotation, str):
            try:
                annotation = eval(annotation, globalns, dict(localns))
            except Exception as e:
                raise TypeError(f"{cls.__qualname__}.{name}: cannot resolve the annotation {annotation!r}: {e}") from e
        resolved[name] = annotation
    return resolved


def _driver_specs(cls: type, localns: Mapping[str, Any], seen: tuple[type, ...] = ()) -> dict[str, _DriverSpec]:
    """The drivers that a member class lists: its own annotations, down to `CompositeClient`."""
    base = _composite_base(cls)
    if base is None or cls in seen:
        return {}
    drivers: dict[str, _DriverSpec] = {}
    declared = cls.__mro__[: cls.__mro__.index(base)]
    for klass in reversed(declared):
        for name, annotation in _own_annotations(klass, localns).items():
            client_class, optional = _split_optional(annotation)
            if isinstance(client_class, type) and issubclass(client_class, DriverClient):
                drivers[name] = _DriverSpec(
                    client_class=client_class,
                    optional=optional,
                    drivers=_driver_specs(client_class, localns, (*seen, cls)),
                )
    return drivers


def _collect_roles(cls: type, localns: Mapping[str, Any]) -> dict[str, _RoleSpec]:
    roles: dict[str, _RoleSpec] = {}
    for klass in reversed(cls.__mro__):
        if klass is LeaseMembers or not issubclass(klass, LeaseMembers):
            continue
        for attribute, annotation in _own_annotations(klass, localns).items():
            marker = klass.__dict__.get(attribute)
            client_class, optional = _split_optional(annotation)
            if _composite_base(client_class) is None:
                if isinstance(marker, _Member):
                    raise TypeError(
                        f"{klass.__qualname__}.{attribute}: member() needs a CompositeClient subclass as "
                        f"the annotation, with `| None` for an optional role"
                    )
                continue
            if attribute in _RESERVED_ATTRIBUTES:
                raise TypeError(
                    f"{klass.__qualname__}.{attribute}: '{attribute}' is an attribute of LeaseMembers. "
                    f"Use member(role=...) on a different attribute for this role"
                )
            role = marker.role if isinstance(marker, _Member) and marker.role else attribute.replace("_", "-")
            previous = roles.get(role)
            if previous is not None and previous.attribute != attribute:
                raise TypeError(
                    f"{klass.__qualname__}: the attributes '{previous.attribute}' and '{attribute}' are both "
                    f"the role '{role}'"
                )
            roles[role] = _RoleSpec(
                role=role,
                attribute=attribute,
                client_class=client_class,
                optional=optional,
                drivers=_driver_specs(client_class, localns),
            )
    return roles


def _class_path(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _adopt(client: DriverClient, expected: type) -> bool:
    """Whether client is an `expected`. A declared composite class takes over the generic one."""
    if isinstance(client, expected):
        return True
    # The exporter reports a plain CompositeClient for a node that only groups
    # drivers. The declared class describes the same node, with its drivers.
    if _composite_base(expected) is not None and _composite_base(type(client)) is type(client):
        try:
            client.__class__ = expected
        except TypeError:
            return False
        return True
    return False


def _check_drivers(owner: str, role: str, path: str, client: DriverClient, drivers: dict[str, _DriverSpec]) -> None:
    for name, spec in drivers.items():
        driver = f"{path}.{name}" if path else name
        child = client.children.get(name)
        if child is None:
            if spec.optional:
                continue
            raise TypeError(
                f"{owner}: role '{role}': the exporter has no driver '{driver}' "
                f"(expected {_class_path(spec.client_class)})"
            )
        if not _adopt(child, spec.client_class):
            reported = child.labels.get(_CLIENT_LABEL) or _class_path(type(child))
            raise TypeError(
                f"{owner}: role '{role}': driver '{driver}' is {reported}, "
                f"expected {_class_path(spec.client_class)}"
            )
        _check_drivers(owner, role, driver, child, spec.drivers)


class LostMember:
    """In the place of the client of a role whose member lost its exporter.

    A client that attaches to a degraded lease gets this object for each
    `Lost` role, and the clients of the other roles. Every use of it raises
    `LeaseMemberLostError` with the role and the reason. Use
    `isinstance(client, LostMember)` to test for it.
    """

    __slots__ = ("_description", "_reason", "_role")

    def __init__(self, role: str, description: str, reason: str | None = None):
        self._role = role
        self._description = description
        self._reason = reason

    def _error(self) -> LeaseMemberLostError:
        return LeaseMemberLostError(self._description, role=self._role, reason=self._reason)

    def __getattr__(self, name: str) -> Any:
        # Special names are what copy, pickle and inspect look for: they get the usual answer.
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        raise self._error()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise self._error()

    def __getitem__(self, key: Any) -> Any:
        raise self._error()

    def __repr__(self) -> str:
        return f"<LostMember {self._role!r}: {self._description}>"


class LeaseMembers:
    """The members of a lease: one driver client tree for each role.

    A subclass declares the roles that a test expects and the drivers of each
    role. The lease template has the selectors:

    .. code-block:: python

        class Phone(CompositeClient):
            adb: AdbClient

        class Projection(LeaseMembers, template="projection"):
            phone: Phone
            headunit: Headunit | None   # optional in the template

        with config.lease(Projection) as lease, lease.connect() as members:
            members.phone.adb.devices()

    An attribute whose annotation is a `CompositeClient` subclass is a role.
    The role name is the attribute name with `-` in the place of `_`;
    `member(role=...)` gives a different name. A class without `template=`
    can only attach to a lease that exists.
    """

    __lease_template__: ClassVar[str | None] = None
    __lease_roles__: ClassVar[dict[str, _RoleSpec]] = {}

    #: Each role of the lease and its client. None for a role without an exporter.
    #: A `LostMember` for a role that lost its exporter: every use of it raises.
    by_role: "Mapping[str, DriverClient | LostMember | None]"
    #: Each role of the lease and its exporter. None for a role without an exporter.
    #: A role that lost its exporter keeps the metadata of that exporter.
    metadata: "Mapping[str, ExporterMetadata | None]"

    def __init_subclass__(cls, template: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if template is not None:
            cls.__lease_template__ = template
        # The class statement runs in the caller's frame: its locals resolve
        # string annotations that name classes of a function body.
        cls.__lease_roles__ = _collect_roles(cls, sys._getframe(1).f_locals)

    def __init__(
        self,
        clients: "Mapping[str, DriverClient | LostMember | None]",
        metadata: "Mapping[str, ExporterMetadata | None]",
    ) -> None:
        for role, client in clients.items():
            # A lost role has no exporter to compare with its member class.
            if client is not None and not isinstance(client, LostMember):
                self.check_client(role, client)
        self.by_role = types.MappingProxyType(dict(clients))
        self.metadata = types.MappingProxyType(dict(metadata))
        for spec in self.__lease_roles__.values():
            setattr(self, spec.attribute, clients.get(spec.role))

    @classmethod
    def check_members(cls, members: Iterable[LeaseMemberModel], source: str) -> list[str]:
        """Compare the declared roles with the members of a template or of a lease.

        Returns the optional roles that the class does not declare. A request
        excludes them.

        :raises TypeError: a declared role is not a member, a required member is
            not declared, or the two do not agree on an optional flag
        """
        if cls is LeaseMembers:
            return []
        by_name = {m.name: m for m in members}
        for role, spec in cls.__lease_roles__.items():
            if role not in by_name:
                raise TypeError(
                    f"{cls.__name__} declares the role '{role}', but {source} has no such role "
                    f"(roles: {', '.join(by_name) or 'none'})"
                )
            if by_name[role].optional and not spec.optional:
                raise TypeError(
                    f"{cls.__name__}.{spec.attribute}: the role '{role}' is optional in {source}, "
                    f"so its annotation must include `| None`"
                )
            if spec.optional and not by_name[role].optional:
                raise TypeError(
                    f"{cls.__name__}.{spec.attribute}: the role '{role}' is required in {source}, "
                    f"so its annotation cannot include `| None`"
                )
        undeclared = [name for name in by_name if name not in cls.__lease_roles__]
        required = [name for name in undeclared if not by_name[name].optional]
        if required:
            raise TypeError(f"{cls.__name__} does not declare the required role '{required[0]}' of {source}")
        return undeclared

    @classmethod
    def check_client(cls, role: str, client: DriverClient) -> None:
        """Compare the exporter of a role with its member class.

        :raises TypeError: a listed driver is missing, or its client class is not the listed one
        """
        spec = cls.__lease_roles__.get(role)
        if spec is None:
            return
        _adopt(client, spec.client_class)
        _check_drivers(cls.__name__, role, "", client, spec.drivers)


class _MemberStub:
    """The exporter stub of one role. A call to a role that lost its exporter says so."""

    def __init__(self, stub: Any, role: str, lease: "MembersLease"):
        self._stub = stub
        self._role = role
        self._lease = lease

    def __getattr__(self, name: str):
        self._lease.raise_if_lost(self._role)
        attribute = getattr(self._stub, name)
        if name != "DriverCall":
            return attribute

        async def driver_call(*args, **kwargs):
            try:
                return await attribute(*args, **kwargs)
            except AioRpcError as e:
                if e.code() in _CONNECTION_LOSS_CODES:
                    await self._lease.refresh_lost(self._role)
                raise

        return driver_call


def _guard_client(client: DriverClient, stub: _MemberStub) -> None:
    client.stub = stub
    for child in client.children.values():
        _guard_client(child, stub)


@dataclass(kw_only=True)
class MembersLease[M: LeaseMembers](Lease):
    """A lease with members: `connect()` gives one client for each role.

    It is made from a template (`template`, with `member_selectors` and
    `excluded_members`), from ad-hoc members (`requested_members`, for the CLI),
    or it attaches to a lease that exists (`name`).
    """

    selector: str | None = None
    duration: timedelta | None = None
    template: str | None = None
    member_selectors: dict[str, str] = field(default_factory=dict)
    excluded_members: list[str] = field(default_factory=list)
    requested_members: list[LeaseMemberModel] = field(default_factory=list)
    constraints: list[LeaseConstraintModel] = field(default_factory=list)
    members_class: type[M] = LeaseMembers  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
    lease_members: list[LeaseMemberModel] = field(default_factory=list, init=False)  # Populated during acquisition
    member_statuses: dict[str, LeaseMemberStatus] = field(default_factory=dict, init=False)
    member_labels: dict[str, dict[str, str]] = field(default_factory=dict, init=False)
    lost: dict[str, str] = field(default_factory=dict, init=False)  # Role to the reason, once it is known
    # Whether this object attached to a lease that existed. Such a client takes a degraded lease as it is.
    attached: bool = field(default=False, init=False)

    @property
    def roles(self) -> list[str]:
        return [m.name for m in self.lease_members]

    def member_exporter(self, role: str) -> str | None:
        """The exporter of a role, or None when the role has none."""
        status = self.member_statuses.get(role)
        return status.exporter if status is not None and status.bound else None

    def lost_member(self, role: str) -> LeaseMemberStatus | None:
        """The status of a role that lost its exporter, or None for any other role."""
        status = self.member_statuses.get(role)
        return status if status is not None and status.lost else None

    async def _create(self):
        template = self.template if self.template is not None else self.members_class.__lease_template__
        if template is None and not self.requested_members:
            raise LeaseError(
                f"{self.members_class.__name__} has no template: it can only attach to a lease that exists "
                "(lease_name=...)"
            )
        duration = self.duration
        excluded = list(self.excluded_members)
        with translate_grpc_exceptions():
            if template is not None:
                definition = await self.svc.GetLeaseTemplate(name=template)
                undeclared = self.members_class.check_members(
                    [entry.member for entry in definition.members], f"the lease template '{template}'"
                )
                excluded.extend(role for role in undeclared if role not in excluded)
                for role in self.member_selectors:
                    if definition.member(role) is None:
                        raise LeaseError(
                            f"the lease template '{template}' has no role '{role}' "
                            f"(roles: {', '.join(definition.role_names())})"
                        )
                for role in excluded:
                    entry = definition.member(role)
                    if entry is None or not entry.optional:
                        raise LeaseError(f"the lease template '{template}' has no optional role '{role}' to exclude")
                if duration is None and definition.default_duration is None:
                    duration = _DEFAULT_DURATION
            logger.debug("Creating lease request for %s", template or "members")
            lease = await self.svc.CreateLease(
                duration=duration,
                lease_id=self.name,
                tags=self.tags or None,
                template=template,
                excluded_members=excluded or None,
                member_selectors=self.member_selectors or None,
                members=self.requested_members or None,
                constraints=self.constraints or None,
            )
            self.name = lease.name
        what = f"template {template}" if template is not None else "members " + ", ".join(
            m.name for m in self.requested_members
        )
        logger.info("Acquiring lease %s for %s", self.name, what)

    async def request_async(self):
        """Request the lease, or attach to it when it has a name.

        A request waits for Ready, and a lost member or a failed hook ends
        it. An attach does not wait for Ready when the lease is degraded:
        `connect()` then gives the bound roles, and a `LostMember` for each
        lost role.

        :raises TypeError: the members class does not agree with the template
        :raises LeaseError: the lease cannot be satisfied, or, for a request, a member is lost or a
            beforeLease hook failed
        """
        self.attached = bool(self.name)
        if self.name:
            logger.debug("using existing lease via env or flag %s", self.name)
            existing_lease = await self.get()
            self._verify_existing_lease(existing_lease)
            if not existing_lease.members:
                raise LeaseError(
                    f"lease {self.name} is a lease of one exporter, without members: "
                    "request it with lease_name alone"
                )
        else:
            await self._create()
        return await self._acquire()

    def _record(self, lease) -> None:
        self.owner = lease.client
        self.lease_members = list(lease.members)
        self.member_statuses = {status.name: status for status in lease.member_statuses}
        self._note_lost(lease)

    def _note_lost(self, lease) -> None:
        for status in lease.lost_members():
            self.lost[status.name] = status.describe_loss()
            self.member_statuses[status.name] = status

    @staticmethod
    def _degraded(result) -> bool:
        return condition_true(result.conditions, "Degraded") or bool(result.lost_members())

    def _raise_for_failure(self, result, *, degraded_attach: bool = False) -> None:
        """Stop the wait for a state that cannot become Ready (DD-16).

        degraded_attach is an attach to a degraded lease: it is used as it
        is, so only a lease that holds no exporters stops it.
        """
        lost = result.lost_members()
        if lost and not degraded_attach:
            raise LeaseError(f"lease {self.name}: {'; '.join(status.describe_loss() for status in lost)}")
        if condition_true(result.conditions, "Unsatisfiable"):
            message = condition_message(result.conditions, "Unsatisfiable")
            raise LeaseError(f"the lease cannot be satisfied: {message}")
        if condition_true(result.conditions, "Invalid"):
            message = condition_message(result.conditions, "Invalid")
            raise LeaseError(f"the lease is invalid: {message}")
        if result.effective_end_time:
            raise LeaseError(f"lease {self.name} has already ended")
        if degraded_attach:
            return
        if condition_present_and_equal(result.conditions, "Ready", "False", "BeforeLeaseHookFailed"):
            message = condition_message(result.conditions, "Ready") or "a beforeLease hook failed"
            raise LeaseError(f"lease {self.name}: beforeLease hook failed (BeforeLeaseHookFailed): {message}")
        if (
            condition_false(result.conditions, "Ready")
            and not condition_true(result.conditions, "Pending")
            and not condition_present_and_equal(result.conditions, "Ready", "False", "BeforeLeaseHook")
        ):
            message = condition_message(result.conditions, "Ready") or "lease is no longer active"
            raise LeaseError(f"lease {self.name}: {message}")

    async def _acquire(self):
        """Wait until the lease is Ready: each required member is bound and each beforeLease hook is complete.

        An attach does not wait for a degraded lease, which cannot become Ready (*Lease state*).
        """
        try:
            with fail_after(self.acquisition_timeout):
                with LeaseAcquisitionSpinner(self.name) as spinner:
                    while True:
                        logger.debug("Polling Lease %s", self.name)
                        result = await self._get_with_retry()
                        degraded = self.attached and self._degraded(result)
                        self._raise_for_failure(result, degraded_attach=degraded)
                        if degraded:
                            self._record(result)
                            reasons = "; ".join(self.lost.values()) or condition_message(result.conditions, "Degraded")
                            spinner.update_status(f"Lease {self.name} is degraded", force=True)
                            logger.warning(
                                "Lease %s is degraded: %s. The other members stay usable.", self.name, reasons
                            )
                            await self._fetch_member_labels()
                            break
                        if condition_true(result.conditions, "Ready"):
                            logger.debug("Lease %s acquired", self.name)
                            spinner.update_status(f"Lease {self.name} acquired successfully!", force=True)
                            self._record(result)
                            await self._fetch_member_labels()
                            break

                        if condition_present_and_equal(result.conditions, "Ready", "False", "BeforeLeaseHook"):
                            message = condition_message(result.conditions, "Ready")
                            spinner.update_status(f"Waiting for beforeLease hooks: {message}")
                        else:
                            self._update_spinner_status(spinner, result)

                        for _ in range(5):
                            await sleep(1)
                            spinner.tick()
            return self

        except TimeoutError:
            logger.debug(f"Lease {self.name} acquisition timed out after {self.acquisition_timeout} seconds")
            raise LeaseError(
                f"lease {self.name} acquisition timed out after {self.acquisition_timeout} seconds"
            ) from None

    async def _fetch_member_labels(self):
        for role, status in self.member_statuses.items():
            # The exporter of a lost role still has its labels, for the metadata of that role.
            if not (status.bound or status.lost) or not status.exporter:
                continue
            try:
                exporter = await self.svc.GetExporter(name=status.exporter)
                self.member_labels[role] = exporter.labels
            except Exception as e:  # noqa: BLE001
                self.member_labels[role] = {}
                logger.warning("Could not fetch labels for exporter %s: %s", status.exporter, e)

    def raise_if_lost(self, role: str) -> None:
        """Raise when the role is known to have lost its exporter."""
        error = self._lost_error(role)
        if error is not None:
            raise error

    def _lost_error(self, role: str) -> LeaseMemberLostError | None:
        description = self.lost.get(role)
        if description is None:
            return None
        status = self.lost_member(role)
        return LeaseMemberLostError(
            f"lease {self.name}: {description}", role=role, reason=status.reason if status is not None else None
        )

    async def refresh_lost(self, role: str) -> None:
        """Read the lease again, and raise when the role lost its exporter."""
        try:
            lease = await self.get(timeout=_RELEASE_RPC_TIMEOUT_SECONDS)
        except Exception:
            logger.debug("Could not check the members of lease %s", self.name, exc_info=True)
            return
        self._note_lost(lease)
        self.raise_if_lost(role)

    async def _on_dial_failed_precondition(self, member_name: str | None) -> None:
        # Dial refuses a member that is not Bound. A Lost member stays lost,
        # so do not read the answer as an exporter that is not ready yet.
        if member_name is not None:
            await self.refresh_lost(member_name)

    def _socket_path(self, directory: str, index: int, role: str) -> Path:
        path = Path(directory) / f"{role}.sock"
        if len(str(path)) > _MAX_SOCKET_PATH:
            path = Path(directory) / f"{index}.sock"
        return path

    @asynccontextmanager
    async def serve_unix_async(self):
        """Serve one local Unix socket for each role that has an exporter.

        Yields a mapping from each role to its socket path. The value is None
        for a role without an exporter. After an attach, that includes a
        role that lost its exporter; `lost_member()` tells the two apart.
        """
        async with AsyncExitStack() as stack:
            directory = stack.enter_context(TemporaryDirectory(dir=xdg_runtime_dir(), prefix="jumpstarter-"))
            paths: dict[str, str | None] = {}
            for index, role in enumerate(self.roles):
                exporter = self.member_exporter(role)
                if exporter is None or (self.attached and role in self.lost):
                    paths[role] = None
                    continue
                self.raise_if_lost(role)
                try:
                    path = await stack.enter_async_context(
                        self._serve_tunnel(role, exporter, self._socket_path(directory, index, role))
                    )
                except LeaseMemberLostError:
                    # The role was lost after the attach read the lease: the other roles stay usable.
                    if not self.attached:
                        raise
                    paths[role] = None
                    continue
                paths[role] = str(path)
            yield paths

    def member_metadata(self) -> "dict[str, ExporterMetadata | None]":
        """The exporter of each role. None for a role without an exporter.

        A role that lost its exporter keeps the metadata of that exporter.
        """
        from jumpstarter.utils.env import ExporterMetadata

        metadata: dict[str, ExporterMetadata | None] = {}
        for role in self.roles:
            status = self.member_statuses.get(role)
            exporter = status.exporter if status is not None and (status.bound or status.lost) else None
            metadata[role] = (
                ExporterMetadata(name=exporter, labels=dict(self.member_labels.get(role, {})), lease=self.name)
                if exporter
                else None
            )
        return metadata

    @asynccontextmanager
    async def connect_async(self, stack):
        self.members_class.check_members(self.lease_members, f"the lease '{self.name}'")
        async with self.serve_unix_async() as paths, AsyncExitStack() as clients_stack:
            clients: dict[str, DriverClient | LostMember | None] = {}
            for role, path in paths.items():
                if path is None:
                    error = self._lost_error(role)
                    clients[role] = LostMember(role, error.message, error.reason) if error is not None else None
                    continue
                client = await clients_stack.enter_async_context(
                    client_from_path(path, self.portal, stack, allow=self.allow, unsafe=self.unsafe)
                )
                _guard_client(client, _MemberStub(client.stub, role, self))
                clients[role] = client
            yield self.members_class(clients, self.member_metadata())

    @contextmanager
    def connect(self) -> Generator[M]:
        with ExitStack() as stack, self.portal.wrap_async_context_manager(self.connect_async(stack)) as members:
            yield members


class LeaseAcquisitionSpinner:
    """Context manager for displaying a spinner during lease acquisition."""

    def __init__(self, lease_name: str | None = None):
        self.lease_name = lease_name
        self.console = Console()
        self.spinner = None
        self.start_time = None
        self._should_show_spinner = self._is_terminal_available() and not self._is_non_interactive()
        self._current_message = None
        self._last_log_time = None
        self._log_throttle_interval = timedelta(minutes=5)

    def _is_non_interactive(self) -> bool:
        """Check if the user desires a NONINTERACTIVE environment."""
        return os.environ.get("NONINTERACTIVE", "false").lower() in ["true", "1"]

    def _is_terminal_available(self) -> bool:
        """Check if we're running in a terminal/TTY."""
        return (
            hasattr(sys.stdout, "isatty")
            and sys.stdout.isatty()
            and hasattr(sys.stderr, "isatty")
            and sys.stderr.isatty()
        )

    def __enter__(self):
        self.start_time = datetime.now(tz=UTC)
        if self._should_show_spinner:
            self.spinner = self.console.status(
                f"Acquiring lease {self.lease_name or '...'}...", spinner="dots", spinner_style="blue"
            )
            self.spinner.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.spinner:
            self.spinner.stop()

    def update_status(self, message: str, force: bool = False):
        """Update the spinner status message.

        :param message: The status message to display
        :param force: If True, always log the message even when throttling (default: False)
        """
        if self.spinner and self._should_show_spinner:
            self._current_message = f"[blue]{message}[/blue]"
            assert self.start_time is not None
            elapsed = datetime.now(tz=UTC) - self.start_time
            elapsed_str = str(elapsed).split(".")[0]  # Remove microseconds
            self.spinner.update(f"{self._current_message} [dim]({elapsed_str})[/dim]")
        else:
            # Log info message when no console is available
            # Throttle updates to at most every 5 minutes unless forced
            now = datetime.now(tz=UTC)
            should_log = (
                force or self._last_log_time is None or (now - self._last_log_time) >= self._log_throttle_interval
            )

            if should_log:
                assert self.start_time is not None
                elapsed = now - self.start_time
                elapsed_str = str(elapsed).split(".")[0]  # Remove microseconds
                logger.info(f"{message} ({elapsed_str})")
                self._last_log_time = now

    def tick(self):
        """Update the spinner with current elapsed time without changing the message."""
        if self.spinner and self._should_show_spinner and self._current_message:
            assert self.start_time is not None
            elapsed = datetime.now(tz=UTC) - self.start_time
            elapsed_str = str(elapsed).split(".")[0]  # Remove microseconds
            # Use the stored current message and update with new elapsed time
            self.spinner.update(f"{self._current_message} [dim]({elapsed_str})[/dim]")
