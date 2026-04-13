import logging
import os
import signal
import sys

import anyio
import click
import grpc
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
    except Exception as e:
        logger.warning(f"PARENT: Error during zombie reaping: {e}")


def _handle_child(config, parsed_bind=None, tls_insecure=False, tls_cert=None, tls_key=None, passphrase=None):  # noqa: C901
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

                # Start signal handler first, then create exporter
        async with create_task_group() as signal_tg:

            # Start signal handler immediately
            signal_tg.start_soon(signal_handler)

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
                    except* Exception as excgroup:
                        _handle_exporter_exceptions(excgroup)
                    exporter_exit_code = exporter.exit_code
            else:
                # Create exporter and run it (controller mode)
                exporter_exit_code = None
                async with config.create_exporter() as exporter:
                    try:
                        await exporter.serve()
                    except* Exception as excgroup:
                        _handle_exporter_exceptions(excgroup)

                    # Check if exporter set an exit code (e.g., from hook failure with on_failure='exit')
                    exporter_exit_code = exporter.exit_code

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
    config, parsed_bind=None, tls_insecure=False, tls_cert=None, tls_key=None, passphrase=None
):
    while True:
        pid = os.fork()

        if pid > 0:
            if (exit_code := _handle_parent(pid)) is not None:
                return exit_code
        else:
            os.setsid() # Become group leader so all spawned subprocesses are reached by parent's signals
            _handle_child(config, parsed_bind, tls_insecure, tls_cert, tls_key, passphrase)
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
    parsed_bind = _parse_listener_bind(listener_bind) if listener_bind is not None else None
    return _serve_with_exc_handling(config, parsed_bind, tls_insecure, tls_cert, tls_key, passphrase)
