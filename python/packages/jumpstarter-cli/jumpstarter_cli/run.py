"""``jmp run`` — run an exporter locally, hosting the Rust core in-process via FFI.

This stays a Python entrypoint (``pip install jumpstarter-all`` keeps ``jmp`` working) and
keeps the previous two-process model: a parent **supervisor** (restart loop, rapid-failure
detection, signal forwarding, PID-1 zombie reaping) forks a **child** that runs the exporter.
What changed is only the child's runtime — instead of the retired Python gRPC exporter, the
child hosts the Rust core in-process and hands off to ``jumpstarter_core.run_exporter``; the
Rust core owns controller registration, lease lifecycle, hooks, status, gRPC, and router
framing, and dispatches the Python driver tree over FFI. No Python gRPC.
"""

import asyncio
import logging
import os
import signal
import sys
import time

import anyio
import click
from anyio import create_task_group, open_signal_receiver
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions

logger = logging.getLogger(__name__)


def _parse_listener_bind(value: str) -> tuple[str, int]:
    """Parse '[host:]port' into (host, port). Default host is 0.0.0.0."""
    if ":" in value:
        host, port_str = value.rsplit(":", 1)
        host = host.strip() or "0.0.0.0"
    else:
        host = "0.0.0.0"
        port_str = value
    try:
        port = int(port_str, 10)
    except ValueError:
        raise click.BadParameter(
            f"port must be an integer, got '{port_str}'", param_hint="'--tls-grpc-listener'"
        ) from None
    if not (1 <= port <= 65535):
        raise click.BadParameter(f"port must be between 1 and 65535, got {port}", param_hint="'--tls-grpc-listener'")
    return host, port


def _handle_exporter_exceptions(excgroup):
    """Handle exceptions from exporter serving."""
    from jumpstarter_cli_common.exceptions import leaf_exceptions
    for exc in leaf_exceptions(excgroup):
        if not isinstance(exc, anyio.get_cancelled_exc_class()):
            click.echo(
                f"Exception while serving on the exporter: {type(exc).__name__}: {exc}",
                err=True,
            )


def _reap_zombie_processes(capture_child=None):
    """Reap zombie processes when running as PID 1."""
    try:
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break # No more children
                if capture_child and pid == capture_child['pid']:
                    capture_child['status'] = status
                logger.debug(f"PARENT: Reaped zombie process {pid} with status {status}")
            except ChildProcessError:
                break # No more children
    except Exception as e:
        logger.warning(f"PARENT: Error during zombie reaping: {e}")


def _handle_child(config):
    """Child process: host the Rust core in-process and serve via FFI, with graceful shutdown."""

    async def serve_with_graceful_shutdown():
        received_signal = 0

        async def signal_handler(cancel_scope):
            with open_signal_receiver(signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT) as signals:
                async for sig in signals:
                    nonlocal received_signal
                    received_signal = sig
                    logger.info("CHILD: Received %d (%s)", received_signal, signal.Signals(received_signal).name)
                    # Cancelling the run_exporter task tears the exporter down. (SIGHUP's
                    # wait-for-lease-exit drain is owned by the Rust core; not yet plumbed
                    # through the FFI run_exporter entrypoint.)
                    cancel_scope.cancel()
                    return

        async with create_task_group() as tg:
            import jumpstarter_core as jc

            from jumpstarter.exporter.host import DriverHostFactory

            # Foreign async callbacks (driver_call / stream_*) run on Rust/tokio worker threads
            # where asyncio.get_running_loop() raises; register this loop with UniFFI so they
            # schedule onto it.
            set_event_loop = getattr(jc, "uniffi_set_event_loop", None)
            if set_event_loop is None:
                from jumpstarter_core import jumpstarter_core as _jc_mod

                set_event_loop = _jc_mod.uniffi_set_event_loop
            set_event_loop(asyncio.get_running_loop())

            tg.start_soon(signal_handler, tg.cancel_scope)

            config_path = str(config.path)
            factory = DriverHostFactory(config_path)
            try:
                await jc.run_exporter(config_path, factory)
            except* Exception as excgroup:
                _handle_exporter_exceptions(excgroup)
            tg.cancel_scope.cancel()

        if received_signal:
            return 128 + received_signal
        return 0

    sys.exit(anyio.run(serve_with_graceful_shutdown))


def _wait_for_child(pid, child_info):
    """Wait for child process, get status from signal handler if reaped."""
    try:
        _, status = os.waitpid(pid, 0)
    except ChildProcessError:
        status = child_info['status']
    return status


def _handle_parent(pid):
    """Handle parent process waiting for child and signal forwarding."""
    child_info = {'pid': pid, 'status': None}

    def parent_signal_handler(signum, _):
        if signum == signal.SIGCHLD and os.getpid() == 1:
            _reap_zombie_processes(capture_child=child_info) # capture our own direct child if reaped
        elif signum != signal.SIGCHLD:
            logger.info("PARENT: Got %d (%s), forwarding to child PG %d", signum, signal.Signals(signum).name, pid)
            if pid > 0:
                try:
                    os.killpg(pid, signum)
                except (ProcessLookupError, OSError):
                    pass

    # Set up signal handlers after fork
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGCHLD):
        signal.signal(sig, parent_signal_handler)

    status = _wait_for_child(pid, child_info)
    if status is None:
        return None

    if os.WIFEXITED(status):
        child_exit_code = os.WEXITSTATUS(status)
        if child_exit_code == 0:
            return None  # restart child (unexpected exit/exception)
        else:
            # Child already encodes signals as 128+N; pass through directly
            return child_exit_code
    else:
        # Child killed by unhandled signal - terminate
        child_exit_signal = os.WTERMSIG(status) if os.WIFSIGNALED(status) else 0
        click.echo(f"Child killed by unhandled signal: {child_exit_signal}", err=True)
        return 128 + child_exit_signal


def _serve_with_exc_handling(config):
    max_rapid_failures = config.failure_detection.max_rapid_failures
    rapid_failure_window = config.failure_detection.rapid_failure_window

    rapid_failure_count = 0
    while True:
        child_start_time = time.monotonic()
        pid = os.fork()

        if pid > 0:
            if (exit_code := _handle_parent(pid)) is not None:
                return exit_code

            # Child exited with code 0 (restart requested).
            # Check if it failed too quickly, indicating a persistent error
            # (e.g., DNS resolution failure) that won't resolve by restarting.
            elapsed = time.monotonic() - child_start_time
            if elapsed < rapid_failure_window:
                rapid_failure_count += 1
                logger.warning(
                    "Child process exited after %.1fs (<%ds), rapid failure %d/%d",
                    elapsed,
                    rapid_failure_window,
                    rapid_failure_count,
                    max_rapid_failures,
                )
                if rapid_failure_count >= max_rapid_failures:
                    click.echo(
                        f"Exporter child process failed {rapid_failure_count} times "
                        f"within {rapid_failure_window}s each. Exiting to allow "
                        f"container/service restart.",
                        err=True,
                    )
                    return 1
            else:
                # Child ran long enough; reset the counter
                if rapid_failure_count > 0:
                    logger.info(
                        "Child ran for %.1fs (>=%ds), resetting rapid failure counter",
                        elapsed,
                        rapid_failure_window,
                    )
                rapid_failure_count = 0
        else:
            os.setsid() # Become group leader so all spawned subprocesses are reached by parent's signals
            _handle_child(config)
            sys.exit(1) # should never happen


@click.command("run")
@opt_config(client=False)
@click.option(
    "--tls-grpc-listener",
    "listener_bind",
    metavar="[HOST:]PORT",
    help="Listen on TCP (and optional TLS) instead of registering with a controller. E.g. 1234 or 0.0.0.0:1234.",
)
@click.option(
    "--tls-grpc-insecure",
    "tls_insecure",
    is_flag=True,
    help="With --tls-grpc-listener, listen without TLS (insecure, for development only).",
)
@click.option(
    "--tls-cert",
    type=click.Path(exists=True),
    help="Server certificate (PEM) for --tls-grpc-listener.",
)
@click.option(
    "--tls-key",
    type=click.Path(exists=True),
    help="Server private key (PEM) for --tls-grpc-listener.",
)
@click.option(
    "--passphrase",
    "passphrase",
    default=None,
    help="Require this passphrase from clients connecting via --tls-grpc-listener.",
)
@handle_exceptions
def run(config, listener_bind, tls_insecure, tls_cert, tls_key, passphrase):
    """Run an exporter locally."""
    if config is None:
        raise click.UsageError("--exporter-config (or --exporter) is required")
    if listener_bind is not None or tls_insecure or tls_cert or tls_key or passphrase:
        # The standalone TCP-listener exporter is owned by the Rust core; it is not yet
        # exposed through the in-process FFI run_exporter entrypoint.
        raise click.UsageError(
            "Standalone listener mode (--tls-grpc-listener / --tls-grpc-insecure / --tls-cert / "
            "--tls-key / --passphrase) is not available via the Python entrypoint yet."
        )
    if config.path is None:
        raise click.UsageError("the resolved exporter config has no on-disk path to run")
    return _serve_with_exc_handling(config)
