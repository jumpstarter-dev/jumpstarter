import logging
import multiprocessing
import os
import signal
import sys
import time
from contextlib import contextmanager

import anyio
import click
import grpc
from anyio import create_task_group, open_signal_receiver
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions

from jumpstarter.metrics import start_metrics_server

logger = logging.getLogger(__name__)

# Phase 2 interim: always expose local HTTP /metrics on ephemeral loopback.
# Phase 3 replaces this with Telemetry reverse-scrape (unix/memory or in-process);
# bind address is intentionally not a user-facing CLI option.
_METRICS_BIND_ADDRESS = ":0"
_WINDOWS_STOP_TIMEOUT = 30.0


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


def _tls_server_credentials(cert_path: str, key_path: str) -> grpc.ServerCredentials:
    """Build gRPC server credentials from PEM cert and key files."""
    with open(cert_path, "rb") as f:
        cert_chain = f.read()
    with open(key_path, "rb") as f:
        private_key = f.read()
    return grpc.ssl_server_credentials(((private_key, cert_chain),))


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
    except Exception as e:  # pragma: no cover  # noqa: BLE001
        logger.warning(f"PARENT: Error during zombie reaping: {e}")


def _handle_child(  # noqa: C901
    config,
    parsed_bind=None,
    tls_insecure=False,
    tls_cert=None,
    tls_key=None,
    passphrase=None,
    control=None,
):
    """Handle child process with graceful shutdown."""
    async def serve_with_graceful_shutdown():  # noqa: C901
        received_signal = 0
        signal_handled = False
        exporter = None

        async def signal_handler():
            nonlocal received_signal, signal_handled

            with open_signal_receiver(signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT) as signals:
                async for sig in signals:
                    if signal_handled:  # ty: ignore[unresolved-reference]
                        continue  # Ignore duplicate signals
                    received_signal = sig
                    logger.info("CHILD: Received %d (%s)", received_signal, signal.Signals(received_signal).name)

                    if exporter:
                        # Terminate exporter. SIGHUP waits until current lease is let go. Later SIGTERM still overrides
                        if received_signal != signal.SIGHUP:
                            signal_handled = True
                        exporter.stop(wait_for_lease_exit=received_signal == signal.SIGHUP, should_unregister=True)

        async def control_handler():
            nonlocal received_signal, signal_handled
            while True:
                if not received_signal:
                    try:
                        if control.poll():
                            received_signal = control.recv()
                    except (EOFError, OSError):
                        # The supervisor disappeared before requesting a stop.
                        received_signal = signal.SIGTERM
                if received_signal and exporter is not None and exporter._tg is not None:
                    signal_handled = True
                    exporter.stop(should_unregister=True)
                    return
                await anyio.sleep(0.05)

        # Start shutdown handling before creating the exporter.
        async with create_task_group() as signal_tg:

            # Start signal handler immediately
            signal_tg.start_soon(signal_handler if control is None else control_handler)

            listen_addr, shutdown_metrics = start_metrics_server(_METRICS_BIND_ADDRESS)
            logger.info("Serving metrics server at http://%s/metrics", listen_addr)

            try:
                if parsed_bind is not None:
                    host, port = parsed_bind
                    tls_credentials = None
                    if tls_insecure:
                        if passphrase:
                            click.echo(
                                "WARNING: --passphrase has no effect without TLS; "
                                "the passphrase will be transmitted in plaintext",
                                err=True,
                            )
                    elif tls_cert and tls_key:
                        tls_credentials = _tls_server_credentials(tls_cert, tls_key)

                    interceptors = None
                    if passphrase:
                        from jumpstarter.exporter.auth import PassphraseInterceptor
                        interceptors = [PassphraseInterceptor(passphrase)]

                    exporter_exit_code = None
                    async with config.create_exporter(standalone=True) as exporter:
                        try:
                            await exporter.serve_standalone_tcp(
                                host, port,
                                tls_credentials=tls_credentials,
                                interceptors=interceptors,
                            )
                        except* Exception as excgroup:  # noqa: BLE001
                            _handle_exporter_exceptions(excgroup)
                        exporter_exit_code = exporter.exit_code
                else:
                    # Create exporter and run it (controller mode)
                    exporter_exit_code = None
                    async with config.create_exporter() as exporter:
                        try:
                            await exporter.serve()
                        except* Exception as excgroup:  # noqa: BLE001
                            _handle_exporter_exceptions(excgroup)

                        # Check if exporter set an exit code (e.g., from hook failure with on_failure='exit')
                        exporter_exit_code = exporter.exit_code
            finally:
                if shutdown_metrics is not None:
                    shutdown_metrics()

            # Cancel the signal handler after exporter completes
            signal_tg.cancel_scope.cancel()

        # Return exit code in priority order:
        # 1. Signal number if received (for signal-based termination)
        # 2. Exporter's exit code if set (for hook failure with on_failure='exit')
        # 3. 0 for immediate restart (normal exit without signal or explicit exit code)
        if received_signal:
            return 128 + received_signal
        elif exporter_exit_code is not None:
            return exporter_exit_code
        else:
            return 0

    sys.exit(anyio.run(serve_with_graceful_shutdown))


def _windows_child(config_json, options, control, logging_settings):
    """Wait for containment before loading drivers or starting subprocesses."""
    from jumpstarter.config.exporter import ExporterConfigV1Alpha1
    from jumpstarter.logging import setup_logging

    # Console events also reach descendants in the supervisor's console group.
    # Only the supervisor handles them; its pipe delivers exactly one stop.
    for sig in (signal.SIGINT, signal.SIGBREAK):
        signal.signal(sig, signal.SIG_IGN)
    try:
        if control.recv() != "start":
            return
        log_format, level = logging_settings
        setup_logging(component="exporter", log_format=log_format, level=level)
        logger.debug("Windows exporter worker started", extra={"pid": os.getpid()})
        config = ExporterConfigV1Alpha1.model_validate_json(config_json)
        _handle_child(config, *options, control=control)
    except EOFError:
        return
    finally:
        control.close()


@contextmanager
def _windows_stop_requests():
    stop_signals = []
    previous = {}

    def request_stop(signum, _frame):
        # Python signal handlers must not block while writing to a pipe.
        stop_signals.append(signum)

    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK):
            previous[sig] = signal.signal(sig, request_stop)
        yield stop_signals
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _wait_windows_child(child, tree, control, stop_signals):
    deadline = None
    while child.is_alive():
        if stop_signals and deadline is None:
            deadline = time.monotonic() + _WINDOWS_STOP_TIMEOUT
            try:
                control.send(stop_signals[0])
            except (EOFError, OSError):
                pass
        if deadline is not None and (len(stop_signals) > 1 or time.monotonic() >= deadline):
            logger.warning("Exporter did not finish graceful shutdown; stopping its process tree")
            tree.close()
            break
        child.join(timeout=0.1)
    child.join(timeout=5)
    if child.is_alive():
        raise RuntimeError("Exporter process tree did not terminate")
    if stop_signals:
        return 128 + stop_signals[0]
    return child.exitcode or None


def _run_windows_child(config, *options):
    """Supervise a spawned exporter without relying on POSIX process APIs."""
    from jumpstarter_cli_common import opt
    from jumpstarter_core.process import ChildProcessTree

    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    logging_settings = (opt._log_format_value, logging.getLogger().level)
    child = context.Process(
        target=_windows_child, args=(config.model_dump_json(by_alias=True), options, child_control, logging_settings),
    )
    tree = None
    started = False
    with _windows_stop_requests() as stop_signals:
        try:
            child.start()
            started = True
            child_control.close()
            tree = ChildProcessTree(child.sentinel)  # The process handle on Windows.
            parent_control.send("start")
            return _wait_windows_child(child, tree, parent_control, stop_signals)
        finally:
            parent_control.close()
            child_control.close()
            try:
                if tree is not None:
                    tree.close()
            finally:
                if started:
                    if child.is_alive():
                        # Assignment failure occurs before the child may start drivers.
                        child.terminate()
                    child.join(timeout=5)
                    if not child.is_alive():
                        child.close()


def _run_child(config, *options):
    if sys.platform == "win32":
        return _run_windows_child(config, *options)
    pid = os.fork()
    if pid > 0:
        return _handle_parent(pid)
    os.setsid()  # Reach all driver subprocesses when forwarding parent signals.
    _handle_child(config, *options)
    sys.exit(1)  # should never happen


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


def _serve_with_exc_handling(
    config,
    parsed_bind=None,
    tls_insecure=False,
    tls_cert=None,
    tls_key=None,
    passphrase=None,
):
    max_rapid_failures = config.failure_detection.max_rapid_failures
    rapid_failure_window = config.failure_detection.rapid_failure_window

    rapid_failure_count = 0
    while True:
        child_start_time = time.monotonic()
        if (exit_code := _run_child(
            config, parsed_bind, tls_insecure, tls_cert, tls_key, passphrase,
        )) is not None:
            return exit_code

        if config.exit_on_lease_end:
            return 0

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
@click.option(
    "--exit-on-lease-end",
    "exit_on_lease_end",
    is_flag=True,
    default=False,
    help="Exit after the current lease ends instead of waiting for a new one.",
)
@handle_exceptions
def run(
    config,
    listener_bind,
    tls_insecure,
    tls_cert,
    tls_key,
    passphrase,
    exit_on_lease_end,
):
    """Run an exporter locally."""
    if listener_bind is not None and config is None:
        raise click.UsageError("--exporter-config (or --exporter) is required when using --tls-grpc-listener")
    if listener_bind is None and (tls_insecure or tls_cert or tls_key or passphrase):
        raise click.UsageError(
            "--tls-grpc-insecure, --tls-cert, --tls-key, and --passphrase require --tls-grpc-listener"
        )
    if listener_bind is not None:
        if tls_insecure and (tls_cert or tls_key):
            raise click.UsageError("--tls-grpc-insecure cannot be combined with --tls-cert / --tls-key")
        if not tls_insecure and not (tls_cert and tls_key):
            raise click.UsageError(
                "--tls-grpc-listener requires either --tls-grpc-insecure or --tls-cert and --tls-key"
            )
    if exit_on_lease_end:
        config.exit_on_lease_end = True
    parsed_bind = _parse_listener_bind(listener_bind) if listener_bind is not None else None
    return _serve_with_exc_handling(
        config,
        parsed_bind,
        tls_insecure,
        tls_cert,
        tls_key,
        passphrase,
    )
