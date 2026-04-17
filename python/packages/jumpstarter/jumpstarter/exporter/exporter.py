import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self

import anyio
import grpc
from anyio import (
    AsyncContextManagerMixin,
    CancelScope,
    connect_unix,
    create_memory_object_stream,
    create_task_group,
    move_on_after,
    sleep,
)
from anyio.abc import TaskGroup
from google.protobuf import empty_pb2
from jumpstarter_protocol import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
)

from jumpstarter.common import ExporterStatus, Metadata, TemporarySocket
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.config.tls import TLSConfigV1Alpha1
from jumpstarter.exporter.hooks import HookExecutor
from jumpstarter.exporter.lease_context import LeaseContext
from jumpstarter.exporter.lease_lifecycle import InvalidTransitionError, LeasePhase
from jumpstarter.exporter.session import Session

if TYPE_CHECKING:
    from jumpstarter.driver import Driver

logger = logging.getLogger(__name__)


async def _standalone_shutdown_waiter():
    """Wait forever; used so serve_standalone_tcp can be cancelled by stop()."""
    await anyio.sleep_forever()


@dataclass(kw_only=True)
class Exporter(AsyncContextManagerMixin, Metadata):
    """Represents a Jumpstarter Exporter runtime instance.

    Inherits from Metadata, which provides:
        uuid: Unique identifier for the exporter instance (UUID4)
        labels: Key-value labels for exporter identification and selector matching
    """

    # Public Configuration Fields

    channel_factory: Callable[[], Awaitable[grpc.aio.Channel]]
    """Factory function for creating gRPC channels to communicate with the controller.

    Called multiple times throughout the exporter lifecycle to establish connections.
    The factory should handle authentication, credentials, and channel configuration.
    Used when creating controller stubs, unregistering, and establishing streams.
    """

    device_factory: Callable[[], "Driver"]
    """Factory function for creating Driver instances representing the hardware/devices.

    Called when creating Sessions to provide access to the underlying device.
    The Driver can contain child drivers in a composite pattern, representing
    the full device tree being exported. Typically created from ExporterConfigV1Alpha1.
    """

    tls: TLSConfigV1Alpha1 = field(default_factory=TLSConfigV1Alpha1)
    """TLS/SSL configuration for secure communication with router and controller.

    Contains certificate authority (ca) and insecure flag for certificate verification.
    Passed to connect_router_stream() when handling client connections.
    Default creates empty config with ca="" and insecure=False.
    """

    grpc_options: dict[str, str] = field(default_factory=dict)
    """Custom gRPC channel options that override or supplement default settings.

    Merged with defaults (round_robin load balancing, keepalive settings, etc.).
    Configured via YAML as grpcOptions in exporter config.
    Passed to connect_router_stream() for client connections.
    """

    hook_executor: HookExecutor | None = field(default=None)
    """Optional executor for lifecycle hooks (before-lease and after-lease).

    When configured, runs custom scripts at key points in the lease lifecycle:
    - before-lease: Runs when transitioning to leased state (setup, validation)
    - after-lease: Runs when transitioning from leased state (cleanup, reset)
    Created when hooks.before_lease or hooks.after_lease are defined in config.
    """

    # Internal State Fields

    _registered: bool = field(init=False, default=False)
    """Tracks whether exporter has successfully registered with the controller.

    Set to True after successful registration. Used to determine if unregistration
    is needed during cleanup.
    """

    _unregister: bool = field(init=False, default=False)
    """Internal flag indicating whether to actively unregister during shutdown.

    Set when stop(should_unregister=True) is called. When False, relies on
    heartbeat timeout for implicit unregistration.
    """

    _stop_requested: bool = field(init=False, default=False)
    """Internal flag indicating a graceful stop has been requested.

    Set to True when stop(wait_for_lease_exit=True) is called. The exporter
    waits for the current lease to exit before stopping.
    """

    _deferred_unregister: bool = field(init=False, default=True)
    """Preserved should_unregister value for deferred stop.

    When stop(wait_for_lease_exit=True) is called, the should_unregister
    preference is stored here and applied when the deferred stop executes.
    """

    _started: bool = field(init=False, default=False)
    """Internal flag tracking whether the exporter has started serving.

    Set to True when the first lease is assigned. Used to determine immediate
    vs graceful stop behavior.
    """

    _tg: TaskGroup | None = field(init=False, default=None)
    """Reference to the anyio TaskGroup managing concurrent tasks.

    Manages streams and connection handling tasks. Used to cancel all tasks
    when stopping. Set during serve() and cleared when done.
    """

    _exporter_status: ExporterStatus = field(init=False, default=ExporterStatus.OFFLINE)
    """Current status of the exporter.

    Updated via _update_status() and reported to controller and session.
    Possible values: OFFLINE, AVAILABLE, BEFORE_LEASE_HOOK, LEASE_READY,
    AFTER_LEASE_HOOK, BEFORE_LEASE_HOOK_FAILED, AFTER_LEASE_HOOK_FAILED.
    """

    _previous_leased: bool = field(init=False, default=False)
    """Previous lease state used to detect lease state transitions.

    Tracks whether the exporter was leased in the previous status check to
    determine when to trigger before-lease and after-lease hooks.
    """

    _exit_code: int | None = field(init=False, default=None)
    """Exit code to use when the exporter shuts down.

    When set to a non-zero value, the exporter should terminate permanently
    (not restart). This is used by hooks with on_failure='exit' to signal
    that the exporter should shut down and not be restarted by the CLI.
    """

    _standalone: bool = field(init=False, default=False)
    """When True, exporter runs without a controller (TCP listener only).

    _report_status and __aexit__ skip controller calls when _standalone is True.
    """

    _lease_context: LeaseContext | None = field(init=False, default=None)
    """Encapsulates all resources associated with the current lease.

    Contains the session, socket path, and synchronization event needed
    throughout the lease lifecycle. This replaces the previous individual
    _current_session, _session_socket_path, and _before_lease_hook fields.

    Lifecycle:
    1. Created in serve() when a lease is assigned (session/socket initially None)
    2. Populated in handle_lease() when the session is created
    3. Accessed by hook execution methods and status reporting
    4. Cleared when lease ends or changes

    The session and socket are managed by the context manager in handle_lease(),
    ensuring proper cleanup when the lease ends. The LeaseScope itself is just
    a reference holder and doesn't manage resource lifecycles directly.
    """

    def stop(self, wait_for_lease_exit=False, should_unregister=False, exit_code: int | None = None):
        """Signal the exporter to stop.

        Args:
            wait_for_lease_exit (bool): If True, wait for the current lease to exit before stopping.
            should_unregister (bool): If True, unregister from controller. Otherwise rely on heartbeat.
            exit_code (int | None): If set, the exporter will exit with this code (non-zero means no restart).
        """
        # Set exit code if provided
        if exit_code is not None:
            self._exit_code = exit_code

        # Stop immediately if not started yet or if immediate stop is requested
        if (not self._started or not wait_for_lease_exit) and self._tg is not None:
            if should_unregister:
                logger.info("Stopping exporter immediately, unregistering from controller")
            else:
                logger.info("Stopping exporter immediately, will not unregister from controller")
            self._unregister = should_unregister
            # Cancel any ongoing tasks
            self._tg.cancel_scope.cancel()
        elif not self._stop_requested:
            self._stop_requested = True
            self._deferred_unregister = should_unregister
            logger.info("Exporter marked for stop upon lease exit")

    @property
    def exit_code(self) -> int | None:
        """Get the exit code for the exporter.

        Returns:
            The exit code if set, or None if the exporter should restart.
        """
        return self._exit_code

    @asynccontextmanager
    async def _controller_stub(self) -> AsyncGenerator[jumpstarter_pb2_grpc.ControllerServiceStub, None]:
        """Create a controller service stub as a context manager.

        Yields:
            ControllerServiceStub connected to the controller

        The underlying channel is automatically closed when the context exits.
        """
        channel = await self.channel_factory()
        try:
            yield jumpstarter_pb2_grpc.ControllerServiceStub(channel)
        finally:
            await channel.close()

    async def _retry_stream(
        self,
        stream_name: str,
        stream_factory: Callable[[jumpstarter_pb2_grpc.ControllerServiceStub], AsyncGenerator],
        send_tx,
        retries: int = 5,
        backoff: float = 1.0,  # Reduced from 3.0 for faster recovery from transient errors
    ):
        """Generic retry wrapper for gRPC streaming calls.

        Args:
            stream_name: Name of the stream for logging purposes
            stream_factory: Function that takes a controller stub and returns an async generator
            send_tx: Transmission channel to send stream items to
            retries: Maximum number of retry attempts
            backoff: Seconds to wait between retries
        """
        retries_left = retries
        while True:
            received_data = False
            try:
                async with self._controller_stub() as controller:
                    logger.debug("%s stream connected to controller", stream_name)
                    async for item in stream_factory(controller):
                        received_data = True
                        logger.debug("%s stream received item", stream_name)
                        await send_tx.send(item)
            except Exception as e:
                if received_data:
                    logger.debug("%s stream retry counter reset after receiving data", stream_name)
                    retries_left = retries
                if retries_left > 0:
                    retries_left -= 1
                    # Check for common transient errors that warrant faster retry
                    error_str = str(e)
                    is_transient = "Stream removed" in error_str or "UNAVAILABLE" in error_str
                    retry_delay = 0.5 if is_transient else backoff
                    logger.info(
                        "%s stream interrupted, restarting in %ss, %s retries left: %s",
                        stream_name,
                        retry_delay,
                        retries_left,
                        e,
                    )
                    await sleep(retry_delay)
                else:
                    raise
            else:
                retries_left = retries

    def _listen_stream_factory(
        self, lease_name: str
    ) -> Callable[[jumpstarter_pb2_grpc.ControllerServiceStub], AsyncGenerator[jumpstarter_pb2.ListenResponse, None]]:
        """Create a stream factory for listening to connection requests."""

        def factory(
            ctrl: jumpstarter_pb2_grpc.ControllerServiceStub,
        ) -> AsyncGenerator[jumpstarter_pb2.ListenResponse, None]:
            return ctrl.Listen(jumpstarter_pb2.ListenRequest(lease_name=lease_name))

        return factory

    def _status_stream_factory(
        self,
    ) -> Callable[[jumpstarter_pb2_grpc.ControllerServiceStub], AsyncGenerator[jumpstarter_pb2.StatusResponse, None]]:
        """Create a stream factory for status updates."""

        def factory(
            ctrl: jumpstarter_pb2_grpc.ControllerServiceStub,
        ) -> AsyncGenerator[jumpstarter_pb2.StatusResponse, None]:
            return ctrl.Status(jumpstarter_pb2.StatusRequest())

        return factory

    async def _register_with_controller(self, local_channel: grpc.aio.Channel):
        """Register the exporter with the controller.

        Args:
            local_channel: The local Unix socket channel to get device reports from
        """
        # Get device reports from the local session
        exporter_stub = jumpstarter_pb2_grpc.ExporterServiceStub(local_channel)
        response: jumpstarter_pb2.GetReportResponse = await exporter_stub.GetReport(empty_pb2.Empty())

        # Register with the REMOTE controller (not the local session)
        logger.info("Registering exporter with controller")
        async with self._controller_stub() as controller:
            await controller.Register(
                jumpstarter_pb2.RegisterRequest(
                    labels=self.labels,
                    reports=response.reports,
                )
            )
        # Mark exporter as registered internally
        self._registered = True
        # Only report AVAILABLE status during initial registration (no lease context)
        # During per-lease registration, status is managed by serve() to avoid
        # overwriting LEASE_READY with AVAILABLE
        if self._lease_context is None:
            await self._report_status(ExporterStatus.AVAILABLE, "Exporter registered and available")

    async def _report_status(self, status: ExporterStatus, message: str = ""):
        """Report the exporter status with the controller and session."""
        self._exporter_status = status

        # Update status in lease context (handles session update internally)
        # This ensures status is stored even before session is created
        if self._lease_context:
            self._lease_context.update_status(status, message)

        if self._standalone:
            logger.debug("Updated status to %s: %s (standalone, no controller)", status, message)
            return

        try:
            async with self._controller_stub() as controller:
                await controller.ReportStatus(
                    jumpstarter_pb2.ReportStatusRequest(
                        status=status.to_proto(),
                        message=message,
                    )
                )
            logger.info(f"Updated status to {status}: {message}")
        except Exception as e:
            logger.error(f"Failed to update status: {e}")

    async def _request_lease_release(self):
        """Request the controller to release the current lease.

        Called after the afterLease hook completes to ensure the lease is
        released even if the client disconnects unexpectedly. This moves
        the lease release responsibility from the client to the exporter.
        """
        if not self._lease_context or not self._lease_context.lease_name:
            logger.debug("No active lease to release")
            return

        lc = self._lease_context.lifecycle

        # If the lease has already ended (controller sent leased=false, or a previous
        # call already released it), skip the release RPC. A stale release_lease=true
        # would release a subsequently-assigned lease on the controller.
        if lc.is_end_requested():
            logger.debug("Lease already ended, skipping release request")
            return

        if self._standalone:
            lc.request_end()
            return

        try:
            async with self._controller_stub() as controller:
                await controller.ReportStatus(
                    jumpstarter_pb2.ReportStatusRequest(
                        status=ExporterStatus.AVAILABLE.to_proto(),
                        message="Lease released after afterLease hook",
                        release_lease=True,
                    )
                )
            logger.info("Requested controller to release lease %s", self._lease_context.lease_name)
        except Exception as e:
            logger.error("Failed to request lease release: %s", e)

        # Directly signal lease ended so handle_lease can exit.
        # The controller may not send another leased=False after our release request,
        # so we signal it ourselves as a fallback.
        if self._lease_context and not lc.is_end_requested():
            lc.request_end()

    async def _unregister_with_controller(self):
        """Safely unregister from controller with timeout and error handling."""
        if not (self._registered and self._unregister):
            return

        logger.info("Unregistering exporter with controller")
        try:
            with move_on_after(10):  # 10 second timeout
                channel = await self.channel_factory()
                try:
                    controller = jumpstarter_pb2_grpc.ControllerServiceStub(channel)
                    await self._report_status(ExporterStatus.OFFLINE, "Exporter shutting down")
                    await controller.Unregister(
                        jumpstarter_pb2.UnregisterRequest(
                            reason="Exporter shutdown",
                        )
                    )
                    logger.info("Controller unregistration completed successfully")
                finally:
                    with CancelScope(shield=True):
                        await channel.close()
        except Exception as e:
            logger.error("Error during controller unregistration: %s", e, exc_info=True)

    @asynccontextmanager
    async def __asynccontextmanager__(self) -> AsyncGenerator[Self]:
        try:
            yield self
        finally:
            try:
                await self._unregister_with_controller()
            except Exception as e:
                logger.error("Error during exporter cleanup: %s", e, exc_info=True)
                # Don't re-raise to avoid masking the original exception

    async def _handle_client_conn(
        self, path: str, endpoint: str, token: str, tls_config: TLSConfigV1Alpha1, grpc_options: dict[str, Any] | None
    ) -> None:
        """Handle a single client connection by proxying between session and router.

        This method establishes a connection from the local session Unix socket to the
        router endpoint, creating a bidirectional proxy that allows the client to
        communicate with the device through the router infrastructure.

        Args:
            path: Unix socket path where the session is serving
            endpoint: Router endpoint URL to connect to
            token: Authentication token for the router connection
            tls_config: TLS configuration for secure router communication
            grpc_options: Optional gRPC channel options for the router connection

        Note:
            This is a private method spawned as a concurrent task by handle_lease_conn()
            for each incoming connection request. It runs until the client disconnects
            or an error occurs.
        """
        try:
            logger.debug("Connecting to session socket at %s", path)
            async with await connect_unix(path) as stream:
                logger.debug("Connected to session, bridging to router at %s", endpoint)
                async with connect_router_stream(endpoint, token, stream, tls_config, grpc_options):
                    logger.debug("Router stream established, forwarding traffic")
        except Exception as e:
            logger.warning("Failed to handle client connection: %s", e)

    async def _handle_end_session(self, lease_context: LeaseContext) -> None:
        """Handle EndSession requests from client.

        Waits for the end_session_requested event, runs the afterLease hook via
        lifecycle transitions, and completes the lifecycle when done. This allows
        clients to receive afterLease hook logs before the connection is closed.

        Args:
            lease_context: The LeaseContext for the current lease.
        """
        lc = lease_context.lifecycle
        logger.debug("_handle_end_session task started, waiting for end_session_requested or lease end")
        async with create_task_group() as wait_tg:

            async def _wait_end_session():
                await lease_context.end_session_requested.wait()
                wait_tg.cancel_scope.cancel()

            async def _wait_lease_end():
                await lc.wait_end_requested()
                wait_tg.cancel_scope.cancel()

            wait_tg.start_soon(_wait_end_session)
            wait_tg.start_soon(_wait_lease_end)

        if lc.is_end_requested() and not lease_context.end_session_requested.is_set():
            logger.debug("Lease ended without EndSession; exiting EndSession handler")
            return

        logger.debug("end_session_requested event received")
        logger.info("EndSession requested, running afterLease hook")

        try:
            await self._run_ending_phase(lease_context)
        except InvalidTransitionError:
            if not lc.is_complete():
                logger.debug("Another task owns the ending phase, waiting for completion")
                await lc.wait_complete()
        except Exception as e:
            logger.error("Error running afterLease hook via EndSession: %s", e)
            try:
                if not lc.is_complete():
                    lc.transition(LeasePhase.FAILED)
            except InvalidTransitionError:
                pass

    @asynccontextmanager
    async def session(self):
        """Create and manage an exporter Session context for initial registration.

        Yields:
            tuple[Session, str]: A tuple of (session, socket_path) for use in lease handling.
        """
        with Session(
            uuid=self.uuid,
            labels=self.labels,
            root_device=self.device_factory(),
        ) as session:
            # Create a Unix socket
            async with session.serve_unix_async() as path:
                # Create a gRPC channel to the controller via the socket
                async with grpc.aio.secure_channel(
                    f"unix://{path}", grpc.local_channel_credentials(grpc.LocalConnectionType.UDS)
                ) as channel:
                    # Register the exporter with the controller
                    await self._register_with_controller(channel)
                # Yield both session and path for creating LeaseScope
                yield session, path

    @asynccontextmanager
    async def session_for_lease(self):
        """Create and manage an exporter Session context with separate hook socket.

        This creates two Unix sockets:
        - Main socket: For client gRPC connections (LogStream, driver calls, etc.)
        - Hook socket: For hook subprocess j commands (isolated to prevent SSL corruption)

        The separation prevents SSL frame corruption that occurs when multiple gRPC
        connections share the same socket simultaneously.

        Note: Registration with the controller is handled once during serve() via
        self.session(). Per-lease sessions do not re-register to avoid spurious
        status updates that can tear down the session prematurely.

        Yields:
            tuple[Session, str, str]: A tuple of (session, main_socket_path, hook_socket_path)
        """
        logger.info("Creating new session for lease")
        with Session(
            uuid=self.uuid,
            labels=self.labels,
            root_device=self.device_factory(),
        ) as session:
            # Create dual Unix sockets - one for clients, one for hooks
            async with session.serve_unix_with_hook_socket_async() as (main_path, hook_path):
                logger.info("Session serving on main=%s, hook=%s", main_path, hook_path)
                yield session, main_path, hook_path
        logger.info("Session closed")

    async def _run_ending_phase(self, lease_scope: LeaseContext) -> None:
        """Transition through ENDING → AFTER_LEASE → RELEASING → DONE.

        Tries to transition to ENDING (if in READY), then runs the after-hook
        if applicable. Raises InvalidTransitionError if another task already
        owns the ending phase.
        """
        lc = lease_scope.lifecycle

        if lc.phase == LeasePhase.READY:
            lc.transition(LeasePhase.ENDING)
        elif lc.phase != LeasePhase.ENDING:
            raise InvalidTransitionError(lc.phase, LeasePhase.ENDING)

        should_run_after = (
            self.hook_executor
            and (lease_scope.has_client() or self._standalone)
            and not lc.skip_after_lease
        )

        if should_run_after:
            lc.transition(LeasePhase.AFTER_LEASE)
            with CancelScope(shield=True):
                await self.hook_executor.run_after_lease_hook(
                    lease_scope,
                    self._report_status,
                    self.stop,
                    self._request_lease_release,
                )
            lc.transition(LeasePhase.RELEASING)
        else:
            if lc.skip_after_lease:
                logger.info("Skipping afterLease hook: beforeLease hook failed")
            lc.transition(LeasePhase.RELEASING)
            if not self._stop_requested:
                await self._report_status(ExporterStatus.AVAILABLE, "Available for new lease")

        lc.transition(LeasePhase.DONE)

    async def _run_before_hook_lifecycle(self, lease_scope: LeaseContext) -> None:
        """Wrap run_before_lease_hook with lifecycle transitions.

        Transitions to BEFORE_LEASE before calling the hook, then transitions
        to READY (or ENDING if end was requested during hook execution).
        """
        lc = lease_scope.lifecycle
        lc.transition(LeasePhase.BEFORE_LEASE)
        try:
            await self.hook_executor.run_before_lease_hook(
                lease_scope,
                self._report_status,
                self.stop,
                self._request_lease_release,
            )
        finally:
            if lc.phase == LeasePhase.BEFORE_LEASE:
                if lc.end_requested:
                    lc.transition(LeasePhase.ENDING)
                else:
                    lc.transition(LeasePhase.READY)

    async def _cleanup_after_lease(self, lease_scope: LeaseContext) -> None:
        """Run afterLease hook cleanup when handle_lease exits.

        This handles the finally-block logic: shielding from cancellation,
        waiting for the before-hook to complete via lifecycle, then running the
        ending phase (after-hook → release → done) through the lifecycle FSM.
        """
        lc = lease_scope.lifecycle
        with CancelScope(shield=True):
            # Wait for lifecycle to reach at least READY before running afterLease.
            # When a lease ends during hook execution, the hook must finish
            # (subject to its configured timeout) before cleanup proceeds.
            safety_timeout = 15
            if self.hook_executor and self.hook_executor.config.before_lease:
                safety_timeout = self.hook_executor.config.before_lease.timeout + 30
            with move_on_after(safety_timeout) as timeout_scope:
                await lc.wait_ready()
            if timeout_scope.cancelled_caught:
                logger.warning("Timed out waiting for lifecycle to reach READY; forcing FAILED")
                try:
                    lc.transition(LeasePhase.FAILED)
                except InvalidTransitionError:
                    pass
                return

            try:
                await self._run_ending_phase(lease_scope)
            except InvalidTransitionError:
                if not lc.is_complete():
                    logger.debug("Another task owns the ending phase, waiting for completion")
                    await lc.wait_complete()
            except Exception as e:
                logger.error("Error during lease cleanup: %s", e)
                try:
                    if not lc.is_complete():
                        lc.transition(LeasePhase.FAILED)
                except InvalidTransitionError:
                    pass

    async def handle_lease(self, lease_name: str, tg: TaskGroup, lease_scope: LeaseContext) -> None:
        """Handle all incoming client connections for a lease.

        This method orchestrates the complete lifecycle of managing connections during
        a lease period. It listens for connection requests and spawns individual
        tasks to handle each client connection.

        The method performs the following steps:
        1. Creates a session for the lease duration
        2. Populates the lease_scope with session and socket path
        3. Sets up a stream to listen for incoming connection requests
        4. Waits for the before-lease hook to complete (if configured)
        5. Spawns a new task for each incoming connection request

        Args:
            lease_name: Name of the lease to handle connections for
            tg: TaskGroup for spawning concurrent connection handler tasks
            lease_scope: LeaseScope with before_lease_hook event (session/socket set here)

        Note:
            This method runs for the entire duration of the lease and is spawned by
            the serve() method when a lease is assigned. It terminates when the lease
            ends or the exporter stops.
        """
        lc = lease_scope.lifecycle
        logger.info("Listening for incoming connection requests on lease %s", lease_name)

        listen_tx, listen_rx = create_memory_object_stream[jumpstarter_pb2.ListenResponse](max_buffer_size=10)

        async with self.session_for_lease() as (session, main_path, hook_path):
            lease_scope.session = session
            lease_scope.socket_path = main_path
            lease_scope.hook_socket_path = hook_path
            session.lease_context = lease_scope
            session.update_status(lease_scope.current_status, lease_scope.status_message)
            logger.debug("Session sockets: main=%s, hook=%s", main_path, hook_path)

            lc.transition(LeasePhase.STARTING)
            logger.info("Accepting connections (driver calls gated until lifecycle reaches READY)")

            tg.start_soon(self._handle_end_session, lease_scope)

            try:
                async with create_task_group() as conn_tg:
                    conn_tg.start_soon(
                        self._retry_stream,
                        "Listen",
                        self._listen_stream_factory(lease_name),
                        listen_tx,
                    )

                    async def wait_for_lease_end():
                        await lc.wait_end_requested()
                        logger.info("Lease end requested, stopping connection handling")
                        conn_tg.cancel_scope.cancel()

                    async def process_connections():
                        await lc.wait_ready()
                        logger.debug("Starting to process connection requests from Listen stream")
                        async for request in listen_rx:
                            logger.info(
                                "Handling new connection request on lease %s (router=%s)",
                                lease_name,
                                request.router_endpoint,
                            )
                            tg.start_soon(
                                self._handle_client_conn,
                                lease_scope.socket_path,
                                request.router_endpoint,
                                request.router_token,
                                self.tls,
                                self.grpc_options,
                            )

                    conn_tg.start_soon(wait_for_lease_end)
                    conn_tg.start_soon(process_connections)

                    if not self.hook_executor:
                        await self._report_status(ExporterStatus.LEASE_READY, "Ready for commands")
                        lc.transition(LeasePhase.READY)
            finally:
                await listen_tx.aclose()
                await self._cleanup_after_lease(lease_scope)

        # Fallback: clear _lease_context if leased→unleased handler didn't fire
        # (e.g., controller didn't send another leased=False after our release request)
        if self._lease_context is lease_scope:
            self._lease_context = None

    async def serve(self):  # noqa: C901
        """
        Serve the exporter.
        """
        # initial registration
        async with self.session():
            pass
        # Buffer status updates to avoid blocking during short processing gaps
        status_tx, status_rx = create_memory_object_stream[jumpstarter_pb2.StatusResponse](max_buffer_size=5)

        async with create_task_group() as tg:
            self._tg = tg
            # Start status stream with retry logic
            tg.start_soon(
                self._retry_stream,
                "Status",
                self._status_stream_factory(),
                status_tx,
            )
            async for status in status_rx:
                # Check for lease state transitions
                previous_leased = self._previous_leased
                current_leased = status.leased

                if self._lease_context is None and status.lease_name != "" and current_leased:
                    self._started = True
                    logger.info("Starting new lease: %s", status.lease_name)
                    lease_scope = LeaseContext(lease_name=status.lease_name)
                    self._lease_context = lease_scope
                    tg.start_soon(self.handle_lease, status.lease_name, tg, lease_scope)

                if current_leased:
                    logger.info("Currently leased by %s under %s", status.client_name, status.lease_name)
                    if self._lease_context:
                        self._lease_context.update_client(status.client_name)

                    if not previous_leased:
                        if self.hook_executor and self._lease_context:
                            tg.start_soon(
                                self._run_before_hook_lifecycle,
                                self._lease_context,
                            )
                else:
                    logger.info("Currently not leased")

                    if previous_leased and self._lease_context:
                        lease_ctx = self._lease_context
                        logger.info("Lease ended, signaling lifecycle")
                        lease_ctx.lifecycle.request_end()

                        with CancelScope(shield=True):
                            await lease_ctx.lifecycle.wait_complete()
                        logger.info("Lease lifecycle completed")

                    self._lease_context = None
                    await sleep(0.2)
                    logger.debug("Ready for next lease")

                    if self._stop_requested:
                        self.stop(should_unregister=self._deferred_unregister)
                        break

                self._previous_leased = current_leased
        self._tg = None

    async def serve_standalone_tcp(
        self,
        host: str,
        port: int,
        *,
        tls_credentials: grpc.ServerCredentials | None = None,
        interceptors: list | None = None,
    ) -> None:
        """Serve the exporter on a TCP address without a controller (standalone mode).

        One session is created and served on host:port (and a temporary Unix socket
        for hook j commands). beforeLease hook runs once if configured; then status
        is set to LEASE_READY. Runs until stop() cancels the task group.
        """
        self._standalone = True
        lease_scope = LeaseContext(lease_name="standalone")
        self._lease_context = lease_scope

        with TemporarySocket() as hook_path:
            hook_path_str = str(hook_path)
            with Session(
                uuid=self.uuid,
                labels=self.labels,
                root_device=self.device_factory(),
            ) as session:
                session.lease_context = lease_scope
                lease_scope.session = session
                lease_scope.socket_path = hook_path_str
                lease_scope.hook_socket_path = hook_path_str

                lc = lease_scope.lifecycle
                lc.transition(LeasePhase.STARTING)

                async with session.serve_tcp_and_unix_async(
                    host, port, hook_path_str,
                    tls_credentials=tls_credentials,
                    interceptors=interceptors,
                ):
                    try:
                        async with create_task_group() as tg:
                            self._tg = tg
                            tg.start_soon(self._handle_end_session, lease_scope)

                            if self.hook_executor:
                                lc.transition(LeasePhase.BEFORE_LEASE)
                                await self.hook_executor.run_before_lease_hook(
                                    lease_scope,
                                    self._report_status,
                                    self.stop,
                                    self._request_lease_release,
                                )
                                if lc.phase == LeasePhase.BEFORE_LEASE:
                                    lc.transition(LeasePhase.READY)
                            else:
                                await self._report_status(ExporterStatus.LEASE_READY, "Ready for commands")
                                lc.transition(LeasePhase.READY)

                            await _standalone_shutdown_waiter()
                    finally:
                        await self._cleanup_after_lease(lease_scope)

        self._lease_context = None
        self._tg = None
