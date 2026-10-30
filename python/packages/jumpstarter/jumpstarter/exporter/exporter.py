import logging
import math
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self

import anyio
import grpc
from anyio import (
    TASK_STATUS_IGNORED,
    AsyncContextManagerMixin,
    CancelScope,
    Event,
    connect_unix,
    create_memory_object_stream,
    create_task_group,
    fail_after,
    move_on_after,
    sleep,
    to_thread,
)
from anyio.abc import TaskGroup, TaskStatus
from google.protobuf import empty_pb2
from jumpstarter_protocol import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
    telemetry_pb2_grpc,
)

from jumpstarter.common import ExporterStatus, Metadata, TemporarySocket
from jumpstarter.common.exceptions import CertificateDiscoveryError
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.config.env import JMP_GRPC_INSECURE, JUMPSTARTER_GRPC_INSECURE
from jumpstarter.config.exporter import DEFAULT_STATUS_STREAM_RETRY_TIMEOUT
from jumpstarter.config.tls import TLSConfigV1Alpha1
from jumpstarter.exporter.fsm import (
    Available,
    ExporterFSM,
    LeaseContext,
    LeaseKey,
    ShutdownMode,
    ShutdownRequest,
    State,
)
from jumpstarter.exporter.hooks import HookExecutor
from jumpstarter.exporter.metrics_stream import MetricsStreamClient
from jumpstarter.exporter.session import Session
from jumpstarter.exporter.telemetry import TelemetryLogHandler
from jumpstarter.logging import clear_log_context, set_log_context

if TYPE_CHECKING:
    from jumpstarter.driver import Driver

logger = logging.getLogger(__name__)

# gRPC retry configuration
# Worst case ~18 min (21 attempts × 30s timeout + backoff sum ~8 min).
# The exporter has nothing useful to do while the controller is unreachable —
# even a restart just fails at _register_with_controller (no retry).
_TRANSIENT_GRPC_CODES = frozenset({grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED})
_RPC_MAX_RETRIES = 20
_RPC_BACKOFF_BASE = 1.0
_RPC_BACKOFF_CAP = 30.0
_RPC_TIMEOUT = 30
_FAIL_FAST_STREAM_CODES = frozenset({
    grpc.StatusCode.UNAUTHENTICATED,
    grpc.StatusCode.PERMISSION_DENIED,
    grpc.StatusCode.NOT_FOUND,
    grpc.StatusCode.INVALID_ARGUMENT,
    grpc.StatusCode.UNIMPLEMENTED,
})
_RESTARTABLE_STREAM_CODES = frozenset({
    grpc.StatusCode.UNAUTHENTICATED,
    grpc.StatusCode.PERMISSION_DENIED,
})
_TEMPORARY_STREAM_FAILURE_EXIT_CODE = 75  # EX_TEMPFAIL; service managers may restart this.


def _is_retryable_stream_error(error: Exception) -> bool:
    if isinstance(error, grpc.aio.AioRpcError):
        return error.code() not in _FAIL_FAST_STREAM_CODES
    return isinstance(error, (CertificateDiscoveryError, OSError))

_IO_CLOSE_TIMEOUT = 10.0
# Release retries are an activity boundary too: shutdown cannot wait through an
# entire (~18 minute) controller retry sequence. Failure still finishes locally.
_LEASE_RELEASE_TIMEOUT = 30.0

_SEVERITY_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def _severity_to_level(severity: str) -> int:
    """Map a JEP-0013 severity string to a Python logging level."""
    key = severity.lower() if severity else ""
    level = _SEVERITY_MAP.get(key)
    if level is None:
        if key:
            import structlog
            structlog.get_logger(__name__).warning(
                "Unrecognized min_severity value, defaulting to info",
                value=severity,
                accepted=list(_SEVERITY_MAP),
            )
        return logging.INFO
    return level


# Sidecar launcher socket injected by the ExporterSet QEMU provisioner.
_LAUNCHER_SOCKET_ENV = "JUMPSTARTER_LAUNCHER_SOCKET"
_DEFAULT_JMP_EXEC = "/shared/jumpstarter-exec"


def shutdown_runtime_sidecar(
    *,
    socket_path: str | None = None,
    binary: str | None = None,
    timeout: float = 10.0,
) -> bool:
    """Ask jumpstarter-exec serve (runtime container PID 1) to exit cleanly.

    With native sidecars (KEP-753), kubelet already terminates ``target-runtime``
    after the exporter (main) container exits, so Pod completion does not depend
    on this call. Invoking ``jumpstarter-exec shutdown`` is best-effort: it
    SIGTERMs in-flight Exec children (e.g. QEMU) for a faster, cleaner teardown
    than waiting for the Pod termination grace period.

    Returns True if a shutdown was attempted successfully, False if no launcher
    socket is configured (non-sidecar / InPlaceReuse hosts) or shutdown failed.

    Callers on the async event loop must offload this via
    ``await to_thread.run_sync(shutdown_runtime_sidecar)``.
    """
    import os
    import subprocess
    from pathlib import Path

    sock = socket_path or os.environ.get(_LAUNCHER_SOCKET_ENV)
    if not sock:
        return False

    exec_bin = Path(binary) if binary else Path(_DEFAULT_JMP_EXEC)
    if not exec_bin.is_file():
        # Fall back to the binary next to the socket (same shared volume).
        candidate = Path(sock).parent / "jumpstarter-exec"
        if candidate.is_file():
            exec_bin = candidate
        else:
            logger.warning(
                "jumpstarter-exec binary not found at %s or %s; cannot shut down runtime",
                _DEFAULT_JMP_EXEC,
                candidate,
            )
            return False

    cmd = [str(exec_bin), "shutdown", "--socket", sock]
    logger.info("Shutting down runtime sidecar via %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    except OSError as e:
        # FileNotFoundError, PermissionError, and other pre-exec failures.
        logger.warning("jumpstarter-exec not executable at %s: %s", exec_bin, e)
        return False
    except subprocess.TimeoutExpired:
        logger.warning("jumpstarter-exec shutdown timed out after %ss", timeout)
        return False

    if result.returncode != 0:
        logger.warning(
            "jumpstarter-exec shutdown exited %s: stdout=%r stderr=%r",
            result.returncode,
            result.stdout,
            result.stderr,
        )
        return False

    logger.info("Runtime sidecar shutdown acknowledged")
    return True


@dataclass(kw_only=True)
class Exporter(AsyncContextManagerMixin, Metadata):
    """Represents a Jumpstarter Exporter runtime instance.

    Inherits from Metadata, which provides:
        uuid: Unique identifier for the exporter instance (UUID4)
        labels: Key-value labels for exporter identification and selector matching
    """

    # Public Configuration Fields

    exporter_name: str = "unknown"

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

    motd: str | None = field(default=None)
    """Message of the day shown to clients when they enter a shell on this exporter.

    Configured via YAML as motd in exporter config and returned to clients in GetReport.
    """

    hook_executor: HookExecutor | None = field(default=None)
    """Optional executor for lifecycle hooks (before-lease and after-lease).

    When configured, runs custom scripts at key points in the lease lifecycle:
    - before-lease: Runs when transitioning to leased state (setup, validation)
    - after-lease: Runs when transitioning from leased state (cleanup, reset)
    Created when hooks.before_lease or hooks.after_lease are defined in config.
    """

    exit_on_lease_end: bool = field(default=False)
    """When True, exit after the first admitted lease has fully closed."""

    status_stream_retry_timeout: float = DEFAULT_STATUS_STREAM_RETRY_TIMEOUT
    """Maximum seconds without a Status item after an error or stream EOF."""

    token: str = field(default="")
    """Bearer token used to authenticate with the telemetry service.

    Set from ExporterConfigV1Alpha1.token so PushLogs calls can include
    the exporter's JWT as an Authorization header.
    """

    # Internal State Fields

    _registered: bool = field(init=False, default=False)
    """Tracks whether exporter has successfully registered with the controller.

    Set to True after successful registration. Used to determine if unregistration
    is needed during cleanup.
    """

    _unregister: bool = field(init=False, default=False)
    """Internal flag indicating whether to actively unregister during shutdown.

    Applied from the merged shutdown request after lease teardown. When False,
    relies on heartbeat timeout for implicit unregistration.
    """

    fsm: ExporterFSM = field(init=False, default_factory=ExporterFSM)
    """The lifecycle in either serving mode; its runner's current_state remains queryable after exit."""

    _background_done: Event = field(init=False, default_factory=Event)
    _shutdown_complete: bool = field(init=False, default=False)

    _tg: TaskGroup | None = field(init=False, default=None)
    """Reference to the anyio TaskGroup managing concurrent tasks.

    Manages streams and connection handling tasks. Used to cancel all tasks
    when stopping. Set during serve() and cleared when done.
    """

    _exporter_status: ExporterStatus = field(init=False, default=ExporterStatus.OFFLINE)
    """Current status of the exporter.

    Updated via report_status() and reported to controller and session.
    Possible values: OFFLINE, AVAILABLE, BEFORE_LEASE_HOOK, LEASE_READY,
    AFTER_LEASE_HOOK, BEFORE_LEASE_HOOK_FAILED, AFTER_LEASE_HOOK_FAILED.
    """

    _exit_code: int | None = field(init=False, default=None)
    """Exit code to use when the exporter shuts down.

    Hook failures with on_failure='exit' use 1 to request shutdown. A Status
    outage or an authentication/authorization error uses 75 to request a
    service-manager restart.
    """

    _controller_stream_failed: bool = field(init=False, default=False)
    """Skip controller RPCs during cleanup after a fatal stream failure."""

    _standalone: bool = field(init=False, default=False)
    """When True, exporter runs without a controller (TCP listener only).

    report_status and __aexit__ skip controller calls when _standalone is True.
    """

    _telemetry_handler: "TelemetryLogHandler | None" = field(init=False, default=None)
    """Optional telemetry log handler that pushes log entries to jumpstarter-telemetry.

    Created after successful registration when GetServiceEndpoints returns a
    telemetry endpoint. None when telemetry is not configured or not available.
    """

    _telemetry_channel: grpc.aio.Channel | None = field(init=False, default=None)
    """gRPC channel to the telemetry service. Closed on exporter shutdown."""

    _metrics_stream: MetricsStreamClient | None = field(init=False, default=None)
    """Optional MetricsStream client. Started next to the log flush loop when telemetry is configured."""

    _status_drain_active: bool = field(init=False, default=False)
    """True only while serve()'s task group is running and the drain task is active.

    When True, report_status enqueues status updates for background processing.
    When False (registration, unregistration), report_status awaits directly.
    """

    _pending_status_request: jumpstarter_pb2.ReportStatusRequest | None = field(init=False, default=None)
    """Latest status update pending delivery by the background drain task.

    "Latest wins" semantics: if multiple report_status calls happen while the
    drain task is busy retrying, only the most recent one is sent.
    """

    _status_rpc_event: Event = field(init=False, default_factory=Event)
    """Signals the drain task that a new status update is pending."""

    @property
    def name(self) -> str:
        return self.exporter_name

    @property
    def exporter_status(self) -> ExporterStatus:
        """Latest reported status; updated only via report_status()."""
        return self._exporter_status

    @property
    def standalone(self) -> bool:
        """True when running without a controller (TCP listener only)."""
        return self._standalone

    def stop(
        self, wait_for_lease_exit: bool = False, should_unregister: bool = False, exit_code: int | None = None
    ) -> None:
        """Publish shutdown intent; activities finish before the next state handles it."""
        self.fsm.inputs.request_shutdown(ShutdownRequest(
            ShutdownMode.DRAIN if wait_for_lease_exit and not self._standalone else ShutdownMode.STOP_NOW,
            exit_code=exit_code,
            should_unregister=should_unregister,
        ))
        shutdown = self.fsm.inputs.snapshot().shutdown
        assert shutdown is not None
        self._exit_code = shutdown.exit_code

    @property
    def exit_code(self) -> int | None:
        """Get the exit code for the exporter.

        Returns:
            The requested exit code, or None if no explicit code was set.
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
            with fail_after(_IO_CLOSE_TIMEOUT, shield=True):
                await channel.close()

    async def _retry_stream(
        self,
        stream_name: str,
        stream_factory: Callable[[jumpstarter_pb2_grpc.ControllerServiceStub], AsyncGenerator],
        send_tx,
        backoff: float = 0.5,
        outage_budget: float | None = None,
    ):
        """Reconnect a controller stream until data resumes or its outage budget expires."""
        delay = backoff
        outage_started = None
        while True:
            deadline = math.inf
            if outage_started is not None and outage_budget is not None:
                deadline = outage_started + outage_budget
            scope = CancelScope(deadline=deadline)
            try:
                with scope:
                    async with self._controller_stub() as controller:
                        async for item in stream_factory(controller):
                            if outage_started is not None:
                                logger.info(
                                    "%s stream reconnected after %.1fs",
                                    stream_name,
                                    anyio.current_time() - outage_started,
                                )
                                outage_started = None
                                scope.deadline = math.inf
                            delay = backoff
                            await send_tx.send(item)
            except Exception as e:
                if not _is_retryable_stream_error(e):
                    if isinstance(e, grpc.aio.AioRpcError):
                        self._controller_stream_failed = True
                        # A new process reloads rotated credentials; PermissionDenied
                        # can also mean the controller temporarily lost API access.
                        self._exit_code = (
                            _TEMPORARY_STREAM_FAILURE_EXIT_CODE
                            if e.code() in _RESTARTABLE_STREAM_CODES else 1
                        )
                    raise
                reason = str(e)
            else:
                reason = "stream ended" if not scope.cancelled_caught else "outage deadline reached"

            now = anyio.current_time()
            if outage_started is None:
                outage_started = now
                logger.warning("%s stream interrupted (%s); retrying", stream_name, reason)
            else:
                logger.debug("%s stream still unavailable: %s", stream_name, reason)

            wait = min(delay, self._remaining_stream_budget(stream_name, now - outage_started, outage_budget))
            # Keep the lease session alive until Status confirms release. EOF
            # needs the same backoff as an error to avoid a busy reconnect loop.
            await sleep(wait)
            delay = min(delay * 2, 5.0)

    def _remaining_stream_budget(self, stream_name: str, elapsed: float, budget: float | None) -> float:
        if budget is None:
            return math.inf
        remaining = budget - elapsed
        if remaining <= 0:
            self._controller_stream_failed = True
            self._exit_code = _TEMPORARY_STREAM_FAILURE_EXIT_CODE
            message = f"{stream_name} stream unavailable for {elapsed:.1f}s (budget {budget:.1f}s)"
            logger.error(message)
            raise TimeoutError(message)
        return remaining

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
        # Registration runs once before the lifecycle; lease phases own later status.
        await self.report_status(ExporterStatus.AVAILABLE, "Exporter registered and available")

        # Discover optional telemetry service endpoint.
        await self._setup_telemetry()

    async def _setup_telemetry(self) -> None:
        """Discover and connect to the optional telemetry service.

        Calls GetServiceEndpoints on the controller. When a telemetry endpoint is
        returned, a gRPC channel is created, TelemetryLogHandler is attached
        to the root Python logger, and a MetricsStreamClient is prepared for
        reverse-scrape. Safe to call multiple times; subsequent calls
        are no-ops if a handler is already configured.
        """
        if self._telemetry_handler is not None:
            return

        try:
            async with self._controller_stub() as controller:
                resp = await controller.GetServiceEndpoints(
                    jumpstarter_pb2.GetServiceEndpointsRequest(),
                    timeout=_RPC_TIMEOUT,
                )
        except grpc.aio.AioRpcError as e:
            # Older controllers that don't support this RPC return UNIMPLEMENTED.
            # Any other error is also non-fatal — telemetry is best-effort.
            logger.debug("GetServiceEndpoints unavailable: %s", e.code())
            return

        if not resp.telemetry_endpoints:
            logger.debug("No telemetry endpoint configured, skipping telemetry setup")
            return

        ep = resp.telemetry_endpoints[0]
        logger.info("Connecting to telemetry service at %s (min_severity=%s)", ep.endpoint, ep.min_severity)

        grpc_insecure = (
            self.tls.insecure
            or os.getenv(JMP_GRPC_INSECURE) == "1"
            or os.getenv(JUMPSTARTER_GRPC_INSECURE) == "1"
        )

        if ep.certificate:
            # Use CA certificate provided by the controller for the telemetry endpoint.
            self._telemetry_channel = grpc.aio.secure_channel(
                ep.endpoint,
                grpc.ssl_channel_credentials(root_certificates=ep.certificate.encode()),
            )
        elif grpc_insecure:
            # Development/testing mode: plaintext gRPC, no TLS at all.
            self._telemetry_channel = grpc.aio.insecure_channel(ep.endpoint)
        else:
            # Production: TLS with system CA pool.
            self._telemetry_channel = grpc.aio.secure_channel(
                ep.endpoint, grpc.ssl_channel_credentials()
            )
        stub = telemetry_pb2_grpc.TelemetryServiceStub(self._telemetry_channel)
        handler = TelemetryLogHandler(stub, namespace=getattr(self, "namespace", "") or "", token=self.token)
        handler.setLevel(_severity_to_level(ep.min_severity))
        logging.getLogger().addHandler(handler)
        self._telemetry_handler = handler
        self._metrics_stream = MetricsStreamClient(
            stub,
            # JWT subject. Not the jumpstarter.dev/name label (#1058).
            identity=self.exporter_name,
            token=self.token,
        )
        logger.info("Telemetry log handler attached")

    async def _retry_rpc(
        self,
        rpc_call: Callable,
        description: str,
        *,
        non_retryable_codes: frozenset[grpc.StatusCode] = frozenset(),
        lease_context: LeaseContext | None = None,
        lease_key: LeaseKey | None = None,
    ) -> tuple[bool, grpc.StatusCode | None]:
        """Retry a unary gRPC call with exponential backoff.

        Args:
            rpc_call: Async callable that takes a controller stub and performs the RPC
            description: Human-readable description for log messages
            non_retryable_codes: gRPC status codes that should cause immediate return
                without retry (e.g. UNIMPLEMENTED for unsupported RPCs)
            lease_context: For release RPCs, stop if this lease is replaced or ended.

        Returns:
            (True, None) on success.
            (False, status_code) on non-retryable error or retry exhaustion.
            (False, None) on non-gRPC exception or stale lease.
        """
        for attempt in range(_RPC_MAX_RETRIES + 1):
            try:
                if lease_context is not None and not self._lease_is_current(lease_context, lease_key):
                    return False, None
                async with self._controller_stub() as controller:
                    # Channel setup also awaits; check immediately before every send.
                    if lease_context is not None and not self._lease_is_current(lease_context, lease_key):
                        logger.debug("Skipping %s for stale lease %s", description, lease_context.lease_name)
                        return False, None
                    await rpc_call(controller)
                return True, None
            except grpc.aio.AioRpcError as e:
                if e.code() in non_retryable_codes:
                    return False, e.code()
                if e.code() in _TRANSIENT_GRPC_CODES and attempt < _RPC_MAX_RETRIES:
                    backoff = min(_RPC_BACKOFF_BASE * (2**attempt), _RPC_BACKOFF_CAP)
                    logger.warning(
                        "Transient error %s (attempt %d/%d), retrying in %.1fs: %s",
                        description,
                        attempt + 1,
                        _RPC_MAX_RETRIES + 1,
                        backoff,
                        e,
                    )
                    await anyio.sleep(backoff)
                    continue
                logger.exception("Failed to %s", description)
                return False, e.code()
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to %s: %s", description, e)
                return False, None
        return False, None

    async def _send_report_status_rpc(self, request: jumpstarter_pb2.ReportStatusRequest) -> bool:
        """Send ReportStatus RPC to the controller with retry on transient errors.

        Returns:
            True if the RPC succeeded, False if unsupported or retries exhausted
        """
        ok, code = await self._retry_rpc(
            lambda ctrl: ctrl.ReportStatus(request, timeout=_RPC_TIMEOUT),
            "report status",
            non_retryable_codes=frozenset({grpc.StatusCode.UNIMPLEMENTED}),
        )
        if not ok and code == grpc.StatusCode.UNIMPLEMENTED:
            # Legacy support: ReportStatus added Nov 2025 (commit b76f6c87).
            # All production controllers support it; safe to remove in future versions.
            logger.warning("ReportStatus not supported by controller, status updates will be skipped")
        return ok

    async def _drain_status_reports(self):
        """Background task that sends status updates to the controller.

        Runs during serve() to make report_status non-blocking. Uses "latest wins"
        semantics: if multiple status updates arrive while retrying, only the most
        recent one is sent. This prevents blocking the client connection path when
        the controller is temporarily unreachable.
        """
        while True:
            await self._status_rpc_event.wait()
            self._status_rpc_event = Event()  # Reset for next update
            request = self._pending_status_request
            if request and await self._send_report_status_rpc(request):
                logger.info(
                    "Updated status to %s: %s",
                    ExporterStatus.from_proto(request.status),
                    request.message,
                )

    async def report_status(
        self, status: ExporterStatus, message: str = "", *, lease: LeaseContext | None = None
    ):
        """Report the exporter status with the controller and optional lease session."""
        self._exporter_status = status

        if lease is not None and lease.session is not None:
            lease.session.update_status(status, message)

        if self._standalone or self._controller_stream_failed:
            logger.debug("Updated local status to %s: %s", status, message)
            return

        request = jumpstarter_pb2.ReportStatusRequest(
            status=status.to_proto(),
            message=message,
        )

        if self._status_drain_active:
            # During serve(): enqueue for background drain (non-blocking)
            self._pending_status_request = request
            self._status_rpc_event.set()
        else:
            # Outside serve() (registration, unregistration): send directly
            if await self._send_report_status_rpc(request):
                logger.info("Updated status to %s: %s", status, message)

    def _lease_is_current(self, lease: LeaseContext, key: LeaseKey | None = None) -> bool:
        """True when key still matches the latest assignment and stop-now does not apply.

        Without a key, the caller is not asking for assignment binding (legacy/test
        paths); the named ReleaseLease RPC still targets lease.lease_name only.
        """
        if key is None:
            return True
        snapshot = self.fsm.inputs.snapshot()
        return (
            snapshot.lease_assignment is not None
            and snapshot.lease_assignment.key == key
            and (snapshot.shutdown is None or snapshot.shutdown.mode != ShutdownMode.STOP_NOW)
        )

    async def request_lease_release(self, lease_scope: LeaseContext, lease_key: LeaseKey | None = None):
        """Request the controller to release lease_scope's lease.

        Called when a lifecycle hook fails with on_failure='endLease'.
        Ordinary hook completion, client disconnection, and exporter cleanup
        must not request release; the client or controller ends those leases.

        The calling state supplies its lease identity, so a delayed retry cannot
        release a replacement. The state owns local completion and status.

        Uses the named ReleaseLease RPC, supported for exporters since controller
        v0.9.0, so retries cannot target a replacement lease.
        """
        if self._controller_stream_failed:
            logger.info("Skipping lease-release RPC after controller stream failure")
            return
        if not self._lease_is_current(lease_scope, lease_key):
            logger.debug("Lease %s no longer active, skipping release request", lease_scope.lease_name)
            return

        if self._standalone:
            return

        ok = False
        with move_on_after(_LEASE_RELEASE_TIMEOUT if lease_key is not None else math.inf):
            ok, _ = await self._retry_rpc(
                lambda ctrl: ctrl.ReleaseLease(
                    jumpstarter_pb2.ReleaseLeaseRequest(name=lease_scope.lease_name), timeout=_RPC_TIMEOUT
                ),
                "release lease",
                lease_context=lease_scope,
                lease_key=lease_key,
            )

        # The assignment may have changed while the RPC was pending.
        if lease_key is not None and not self._lease_is_current(lease_scope, lease_key):
            return
        if ok:
            logger.info("Released lease %s via ReleaseLease RPC", lease_scope.lease_name)
        else:
            logger.warning("Failed to release lease %s; the lifecycle will finish locally", lease_scope.lease_name)

    async def _unregister_with_controller(self):
        """Safely unregister from controller with timeout and error handling."""
        if not (self._registered and self._unregister):
            return

        logger.info("Unregistering exporter with controller")
        try:
            with move_on_after(10, shield=True):
                channel = await self.channel_factory()
                try:
                    controller = jumpstarter_pb2_grpc.ControllerServiceStub(channel)
                    await self.report_status(ExporterStatus.OFFLINE, "Exporter shutting down")
                    await controller.Unregister(
                        jumpstarter_pb2.UnregisterRequest(
                            reason="Exporter shutdown",
                        )
                    )
                    logger.info("Controller unregistration completed successfully")
                finally:
                    with fail_after(_IO_CLOSE_TIMEOUT, shield=True):
                        await channel.close()
        except Exception as e:  # noqa: BLE001
            logger.error("Error during controller unregistration: %s", e)

    @asynccontextmanager
    async def __asynccontextmanager__(self) -> AsyncGenerator[Self]:
        try:
            yield self
        finally:
            try:
                await self._unregister_with_controller()
            except Exception:
                logger.exception("Error during exporter cleanup")
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
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to handle client connection: %s", e)

    @asynccontextmanager
    async def session(self):
        """Create and manage an exporter Session context for initial registration.

        Yields:
            tuple[Session, str]: A tuple of (session, socket_path) for use in lease handling.
        """
        with Session(
            uuid=self.uuid,
            labels=self.labels,
            exporter_name=self.exporter_name,
            root_device=self.device_factory(),
            motd=self.motd,
        ) as session:
            # Create a Unix socket
            async with session.serve_unix_async() as path:
                # Create a gRPC channel to the controller via the socket
                channel = grpc.aio.secure_channel(
                    f"unix://{path}", grpc.local_channel_credentials(grpc.LocalConnectionType.UDS)
                )
                try:
                    # Register the exporter with the controller
                    await self._register_with_controller(channel)
                finally:
                    with fail_after(_IO_CLOSE_TIMEOUT, shield=True):
                        await channel.close()
                yield session, path

    @asynccontextmanager
    async def session_for_lease(self) -> AsyncGenerator[tuple[Session, str, str]]:
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
            exporter_name=self.exporter_name,
            root_device=self.device_factory(),
            motd=self.motd,
        ) as session:
            # Create dual Unix sockets - one for clients, one for hooks
            async with session.serve_unix_with_hook_socket_async() as (main_path, hook_path):
                logger.info("Session serving on main=%s, hook=%s", main_path, hook_path)
                yield session, str(main_path), str(hook_path)
        logger.info("Session closed")

    async def _run_stream(self, name, factory, sender, outage_budget=None) -> None:
        """Turn classified terminal stream failures into deferred shutdown intent."""
        try:
            await self._retry_stream(name, factory, sender, outage_budget=outage_budget)
        except (grpc.aio.AioRpcError, TimeoutError):
            if not self._controller_stream_failed:
                raise
            logger.exception("%s stream failed; stopping after the current activity", name)
            self.stop(exit_code=self._exit_code, should_unregister=False)

    async def _serve_lease_connections(
        self, lease: LeaseContext, done: Event, *, task_status: TaskStatus[CancelScope] = TASK_STATUS_IGNORED
    ) -> None:
        """Own the lease's I/O task group in this task, independently of FSM calls."""
        tx, rx = create_memory_object_stream[jumpstarter_pb2.ListenResponse](10)
        try:
            async with tx, rx, create_task_group() as connections:
                connections.start_soon(self._run_stream, "Listen", self._listen_stream_factory(lease.lease_name), tx)
                task_status.started(connections.cancel_scope)
                # Buffer remote Dials until the before-hook outcome is published.
                # Hooks can already use their local socket; remote driver calls
                # must not bypass setup just because that socket allows hook RPCs.
                assert lease.session is not None
                while lease.session._current_status == ExporterStatus.BEFORE_LEASE_HOOK:
                    await lease.session._status_update_event.wait()
                async for request in rx:
                    connections.start_soon(
                        self._handle_client_conn,
                        lease.socket_path,
                        request.router_endpoint,
                        request.router_token,
                        self.tls,
                        self.grpc_options,
                    )
        finally:
            done.set()

    @asynccontextmanager
    async def lease_connections(self, lease: LeaseContext) -> AsyncGenerator[None]:
        assert self._tg is not None
        done = Event()
        scope = await self._tg.start(self._serve_lease_connections, lease, done)
        try:
            yield
        finally:
            scope.cancel()
            with fail_after(_IO_CLOSE_TIMEOUT, shield=True):
                await done.wait()

    async def _serve_background(self, *, task_status: TaskStatus[None] = TASK_STATUS_IGNORED) -> None:
        try:
            async with create_task_group() as background:
                self._tg = background
                self._status_drain_active = True
                background.start_soon(self._drain_status_reports)
                self._start_telemetry_tasks(background)
                background.start_soon(
                    self._run_stream, "Status", self._status_stream_factory(), self.fsm.inputs,
                    self.status_stream_retry_timeout,
                )
                task_status.started()
                await anyio.sleep_forever()
        finally:
            self._background_done.set()

    def _start_telemetry_tasks(self, tg: TaskGroup) -> None:
        """Start PushLogs flush and MetricsStream next to the control-plane tasks."""
        if self._telemetry_handler is not None:
            tg.start_soon(self._telemetry_handler.flush_loop)
        if self._metrics_stream is not None:
            tg.start_soon(self._metrics_stream.run)

    async def finish_shutdown(self) -> None:
        """Stop/join producers after lease cleanup, then close exporter-wide services."""
        if self._shutdown_complete:
            return
        if self._tg is not None:
            self._tg.cancel_scope.cancel()
            with fail_after(_IO_CLOSE_TIMEOUT, shield=True):
                await self._background_done.wait()
            self._tg = None
        self._status_drain_active = False
        shutdown = self.fsm.inputs.snapshot().shutdown
        self._unregister = shutdown is not None and shutdown.should_unregister and not self._controller_stream_failed
        if self._unregister and self._registered:
            await self._unregister_with_controller()
        else:
            with move_on_after(10, shield=True):
                await self.report_status(ExporterStatus.OFFLINE, "Exporter shutting down")
        self._unregister = False
        self._registered = False
        if self._telemetry_handler is not None:
            handler, self._telemetry_handler = self._telemetry_handler, None
            logging.getLogger().removeHandler(handler)
            try:
                # Telemetry is best-effort; a full queue must not multiply RPC timeouts.
                with move_on_after(10, shield=True):
                    await handler.close_async()
            finally:
                handler.close()
        if self._telemetry_channel is not None:
            with fail_after(_IO_CLOSE_TIMEOUT, shield=True):
                await self._telemetry_channel.close()
            self._telemetry_channel = None
        self._metrics_stream = None
        if self.exit_on_lease_end and not self._standalone:
            with CancelScope(shield=True):
                await to_thread.run_sync(shutdown_runtime_sidecar)
        self._shutdown_complete = True

    async def _run_lifecycle(self, initial: State) -> None:
        try:
            await self.fsm.run(initial, name=self.exporter_name)
        finally:
            await self.finish_shutdown()

    async def serve(self) -> None:
        """Run the controller-mode lifecycle in one task with background input producers."""
        set_log_context(exporter=self.exporter_name)
        try:
            async with self.session():
                pass
            async with create_task_group() as supervisor:
                try:
                    await supervisor.start(self._serve_background)
                    await self._run_lifecycle(Available(self))
                finally:
                    # Stopping handles the normal path; errors/cancellation use the same cleanup.
                    await self.finish_shutdown()
                    supervisor.cancel_scope.cancel()
        finally:
            try:
                await self.finish_shutdown()
            finally:
                clear_log_context()

    async def serve_standalone_tcp(
        self,
        host: str,
        port: int,
        *,
        tls_credentials: grpc.ServerCredentials | None = None,
        interceptors: list | None = None,
    ) -> None:
        """Run one standalone TCP session through the same lifecycle states."""
        self._standalone = True
        self.fsm.inputs.publish_lease_assignment("standalone")
        set_log_context(exporter=self.exporter_name, lease_id="standalone")
        try:
            context = self._standalone_session(host, port, tls_credentials=tls_credentials, interceptors=interceptors)
            await self._run_lifecycle(Available(self, session_context=context))
        finally:
            clear_log_context()

    @asynccontextmanager
    async def _standalone_session(
        self, host: str, port: int, *, tls_credentials: grpc.ServerCredentials | None, interceptors: list | None
    ) -> AsyncGenerator[tuple[Session, str, str]]:
        with TemporarySocket() as hook_path:
            hook_path_str = str(hook_path)
            with Session(
                uuid=self.uuid,
                labels=self.labels,
                exporter_name=self.exporter_name,
                root_device=self.device_factory(),
                motd=self.motd,
            ) as session:
                # Available sets session.lease_context after enter with the admitting lease.
                async with session.serve_tcp_and_unix_async(
                    host, port, hook_path_str,
                    tls_credentials=tls_credentials,
                    interceptors=interceptors,
                ):
                    yield session, hook_path_str, hook_path_str
