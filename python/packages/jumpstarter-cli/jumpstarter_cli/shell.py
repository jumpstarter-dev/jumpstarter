import logging
import sys
from contextlib import ExitStack
from datetime import timedelta

import anyio
import click
from anyio import create_task_group, get_cancelled_exc_class
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import find_exception_in_group, handle_exceptions_with_reauthentication
from jumpstarter_cli_common.oidc import (
    TOKEN_EXPIRY_WARNING_SECONDS,
    format_duration,
    get_token_remaining_seconds,
)
from jumpstarter_cli_common.signal import signal_handler

from .common import opt_acquisition_timeout, opt_duration_partial, opt_selector
from .login import relogin_client
from jumpstarter.client.client import client_from_path
from jumpstarter.common import ExporterStatus
from jumpstarter.common.exceptions import ConnectionError, ExporterOfflineError
from jumpstarter.common.utils import launch_shell
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.exporter import ExporterConfigV1Alpha1

logger = logging.getLogger(__name__)


def _run_shell_only(lease, config, command, path: str) -> int:
    """Run just the shell command without log streaming."""
    return launch_shell(
        path,
        lease.exporter_name,
        config.drivers.allow,
        config.drivers.unsafe,
        config.shell.use_profiles,
        command=command,
        lease=lease,
    )


def _warn_about_expired_token(lease_name: str, selector: str) -> None:
    """Warn user that lease won't be cleaned up due to expired token."""
    click.echo(click.style("\nToken expired - lease cleanup will fail.", fg="yellow", bold=True))
    click.echo(click.style(f"Lease '{lease_name}' will remain active.", fg="yellow"))
    click.echo(click.style(f"To reconnect: JMP_LEASE={lease_name} jmp shell", fg="cyan"))


async def _monitor_token_expiry(config, cancel_scope) -> None:
    """Monitor token expiry and warn user."""
    token = getattr(config, "token", None)
    if not token:
        return

    warned = False
    while not cancel_scope.cancel_called:
        try:
            remaining = get_token_remaining_seconds(token)
            if remaining is None:
                return

            if remaining <= 0:
                click.echo(click.style("\nToken expired! Exiting shell.", fg="red", bold=True))
                cancel_scope.cancel()
                return

            if remaining <= TOKEN_EXPIRY_WARNING_SECONDS and not warned:
                duration = format_duration(remaining)
                click.echo(
                    click.style(
                        f"\nToken expires in {duration}. Session will continue but cleanup may fail on exit.",
                        fg="yellow",
                        bold=True,
                    )
                )
                warned = True

            await anyio.sleep(30)
        except Exception:
            return


async def _run_shell_with_lease_async(lease, exporter_logs, config, command, cancel_scope):  # noqa: C901
    """Run shell with lease context managers and wait for afterLease hook if logs enabled.

    When exporter_logs is enabled, this function will:
    1. Connect and start log streaming with background status monitor
    2. Wait for beforeLease hook to complete (logs stream in real-time)
    3. Run the shell command
    4. After shell exits, call EndSession to trigger and wait for afterLease hook
    5. Logs stream to client during hook execution
    6. Release the lease after hook completes

    Uses non-blocking polling via StatusMonitor for robust status tracking.
    If Ctrl+C is pressed during EndSession, the wait is skipped but the lease is still released.
    """
    async with lease.serve_unix_async() as path:
        async with lease.monitor_async():
            # Use ExitStack for the client (required by client_from_path)
            with ExitStack() as stack:
                async with client_from_path(
                    path, lease.portal, stack, allow=lease.allow, unsafe=lease.unsafe
                ) as client:
                    # Probe GetStatus before log stream so the server-side error
                    # from unsupported exporters is not streamed to the terminal.
                    await client.get_status_async()

                    # Start log streaming and status monitor together
                    # The status monitor polls in the background for reliable status tracking
                    async with client.log_stream_async(show_all_logs=exporter_logs):
                        async with client.status_monitor_async(poll_interval=0.3) as monitor:
                            # Wait for beforeLease hook to complete while logs are streaming
                            # This allows hook output to be displayed in real-time
                            # Uses non-blocking polling instead of streaming for robustness
                            logger.info("Waiting for beforeLease hook to complete...")

                            # Wait for LEASE_READY or hook failure using background monitor
                            result = await monitor.wait_for_any_of(
                                [ExporterStatus.LEASE_READY, ExporterStatus.BEFORE_LEASE_HOOK_FAILED], timeout=300.0
                            )

                            if result == ExporterStatus.BEFORE_LEASE_HOOK_FAILED:
                                reason = monitor.status_message or "beforeLease hook failed"
                                raise ExporterOfflineError(reason)
                            elif result is None:
                                if monitor.connection_lost:
                                    # Connection lost while waiting for hook — lease expired
                                    logger.info("Lease expired while waiting for beforeLease hook to complete")
                                    return 0
                                else:
                                    reason = monitor.status_message or "Timeout waiting for beforeLease hook"
                                    raise ExporterOfflineError(reason)

                            logger.debug("Exporter ready (status: %s), launching shell...", result)

                            # Run the shell command
                            exit_code = await anyio.to_thread.run_sync(_run_shell_only, lease, config, command, path)

                            # Shell has exited. For auto-created leases (release=True), call
                            # EndSession to trigger afterLease hook while keeping log stream
                            # and status monitor open. For pre-created leases (release=False),
                            # skip EndSession so the exporter stays in LEASE_READY and the
                            # user can reconnect later.
                            if (
                                lease.release
                                and lease.name
                                and not cancel_scope.cancel_called
                                and not monitor._get_status_unsupported
                            ):
                                # Quick probe to catch exporter restarts the slow-poll loop
                                # (5s interval in LEASE_READY) may not have detected yet.
                                if not monitor.connection_lost:
                                    try:
                                        probe_status = await client.get_status_async()
                                        if probe_status is not None and probe_status not in (
                                            ExporterStatus.LEASE_READY,
                                            ExporterStatus.AFTER_LEASE_HOOK,
                                        ):
                                            logger.debug(
                                                "Exporter in unexpected state (%s), skipping afterLease hook",
                                                probe_status,
                                            )
                                            monitor._connection_lost = True
                                    except Exception:
                                        logger.debug("Connection probe failed, marking connection as lost")
                                        monitor._connection_lost = True

                                if monitor.connection_lost:
                                    logger.debug("Connection already lost, skipping afterLease hook")
                                else:
                                    logger.info("Running afterLease hook (Ctrl+C to skip)...")
                                    try:
                                        # EndSession triggers the afterLease hook asynchronously
                                        # Wrap in anyio timeout as safety net in case gRPC deadline
                                        # doesn't fire on a broken channel (e.g. lease timeout)
                                        success = False
                                        with anyio.move_on_after(10):
                                            success = await client.end_session_async()
                                        if success:
                                            # Wait for hook to complete using background monitor
                                            # This allows afterLease logs to be displayed in real-time
                                            result = await monitor.wait_for_any_of(
                                                [ExporterStatus.AVAILABLE, ExporterStatus.AFTER_LEASE_HOOK_FAILED],
                                                timeout=30.0,
                                            )
                                            if result == ExporterStatus.AVAILABLE:
                                                logger.info("afterLease hook completed")
                                            elif result == ExporterStatus.AFTER_LEASE_HOOK_FAILED:
                                                reason = monitor.status_message or "afterLease hook failed"
                                                raise ExporterOfflineError(reason)
                                            elif monitor.connection_lost:
                                                # If connection lost during AFTER_LEASE_HOOK, the hook
                                                # likely failed and the exporter shut down (onFailure=exit)
                                                if monitor.current_status == ExporterStatus.AFTER_LEASE_HOOK:
                                                    reason = "afterLease hook failed (connection lost)"
                                                    raise ExporterOfflineError(reason)
                                                # Connection lost but hook wasn't running. This is expected when
                                                # the lease times out — exporter handles its own cleanup.
                                                logger.info("Connection lost, skipping afterLease hook wait")
                                            elif result is None:
                                                logger.warning("Timeout waiting for afterLease hook to complete")
                                        else:
                                            logger.debug("EndSession not implemented, skipping hook wait")
                                    except ExporterOfflineError:
                                        raise
                                    except Exception as e:
                                        logger.warning("Error during afterLease hook: %s", e)

                            return exit_code


async def _shell_with_signal_handling(  # noqa: C901
    config, selector, lease_name, duration, exporter_logs, command, acquisition_timeout
):
    """Handle lease acquisition and shell execution with signal handling."""
    exit_code = 0
    cancelled_exc_class = get_cancelled_exc_class()
    lease_used = None

    # Check token before starting
    token = getattr(config, "token", None)
    if token:
        remaining = get_token_remaining_seconds(token)
        if remaining is not None and remaining <= 0:
            raise ConnectionError("token is expired")

    async with create_task_group() as tg:
        tg.start_soon(signal_handler, tg.cancel_scope)

        try:
            try:
                async with anyio.from_thread.BlockingPortal() as portal:
                    async with config.lease_async(selector, lease_name, duration, portal, acquisition_timeout) as lease:
                        lease_used = lease

                        # Start token monitoring only once we're in the shell
                        tg.start_soon(_monitor_token_expiry, config, tg.cancel_scope)

                        exit_code = await _run_shell_with_lease_async(
                            lease, exporter_logs, config, command, tg.cancel_scope
                        )
            except BaseExceptionGroup as eg:
                for exc in eg.exceptions:
                    if isinstance(exc, TimeoutError):
                        raise exc from None
                offline_exc = find_exception_in_group(eg, ExporterOfflineError)
                if offline_exc:
                    raise offline_exc from None
                if lease_used is not None:
                    raise ExporterOfflineError("Connection to exporter lost") from None
                raise
            except cancelled_exc_class:
                # Check if cancellation was due to token expiry
                token = getattr(config, "token", None)
                if lease_used and token:
                    remaining = get_token_remaining_seconds(token)
                    if remaining is not None and remaining <= 0:
                        _warn_about_expired_token(lease_used.name, selector)
                exit_code = 2
        finally:
            if not tg.cancel_scope.cancel_called:
                tg.cancel_scope.cancel()

    return exit_code


@click.command("shell")
@opt_config()
@click.argument("command", nargs=-1)
# client specific
# TODO: warn if these are specified with exporter config
@click.option("--lease", "lease_name")
@opt_selector
@opt_duration_partial(default=timedelta(minutes=30), show_default="00:30:00")
@click.option("--exporter-logs", is_flag=True, help="Enable exporter log streaming")
@opt_acquisition_timeout()
# end client specific
@handle_exceptions_with_reauthentication(relogin_client)
def shell(config, command: tuple[str, ...], lease_name, selector, duration, exporter_logs, acquisition_timeout):
    """
    Spawns a shell (or custom command) connecting to a local or remote exporter

    COMMAND is the custom command to run instead of shell.

    Example:

    .. code-block:: bash

        $ jmp shell --exporter foo -- python bar.py
    """

    match config:
        case ClientConfigV1Alpha1():
            exit_code = anyio.run(
                _shell_with_signal_handling,
                config,
                selector,
                lease_name,
                duration,
                exporter_logs,
                command,
                acquisition_timeout,
            )
            sys.exit(exit_code)

        case ExporterConfigV1Alpha1():
            with config.serve_unix() as path:
                # SAFETY: the exporter config is local thus considered trusted
                launch_shell(
                    path,
                    "local",
                    allow=[],
                    unsafe=True,
                    use_profiles=False,
                    command=command,
                )
