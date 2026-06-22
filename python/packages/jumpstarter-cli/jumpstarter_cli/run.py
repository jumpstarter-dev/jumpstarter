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
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

import anyio
import click
from anyio import create_task_group, open_signal_receiver
from jumpstarter_cli_common.exceptions import handle_exceptions

logger = logging.getLogger(__name__)

# Exporter config locations (mirrors jumpstarter.config.exporter, without the pydantic model):
# the user config dir, then a system dir for systemd units / containers mounting /etc/jumpstarter.
_SYSTEM_EXPORTERS_PATH = Path("/etc/jumpstarter/exporters")


def _user_exporters_path() -> Path:
    from jumpstarter.common.xdg import xdg_config_home
    from jumpstarter.config.env import JMP_CLIENT_CONFIG_HOME

    base = Path(os.getenv(JMP_CLIENT_CONFIG_HOME) or (xdg_config_home() / "jumpstarter"))
    return base / "exporters"


def _resolve_exporter_config_path(alias: str | None, path: str | None) -> Path | None:
    """Resolve ``--exporter <alias>`` / ``--exporter-config <path>`` to a config file path.

    An explicit ``--exporter-config`` path wins; an ``--exporter`` alias is looked up in the user
    config dir, then the system dir (matching ``ExporterConfigV1Alpha1.resolve_path``). The Rust
    core (``jc.load_exporter_spec``) parses the file — Python only locates it.
    """
    if path:
        return Path(path)
    if alias:
        user = _user_exporters_path() / f"{alias}.yaml"
        if user.exists():
            return user
        system = _SYSTEM_EXPORTERS_PATH / f"{alias}.yaml"
        if system.exists():
            return system
        # Fall back to the user path so the caller surfaces a "does not exist" error against it.
        return user
    return None


def _failure_detection(config_path: Path) -> tuple[int, int]:
    """Read ``failureDetection.{maxRapidFailures,rapidFailureWindow}`` from the exporter config,
    parsing the YAML via the Rust core (``jc.parse_yaml``); falls back to the defaults (5, 60)."""
    import jumpstarter_core as jc

    data = json.loads(jc.parse_yaml(config_path.read_text()))
    fd = data.get("failureDetection") or {}
    return int(fd.get("maxRapidFailures", 5)), int(fd.get("rapidFailureWindow", 60))


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


# Child exit code signalling a terminal exporter shutdown (a shutdown signal or an
# `on_failure: exit` hook) that the parent must NOT restart — distinct from a clean exit
# code 0, which means "recoverable exit, restart the child".
_EXPORTER_SHUTDOWN_EXIT_CODE = 99


def _handle_child(config_path):
    """Child process: host the Rust core in-process and serve via FFI, with graceful shutdown."""

    async def serve_with_graceful_shutdown():
        received_signal = 0
        exit_kind = None

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

            tg.start_soon(signal_handler, tg.cancel_scope)

            # The polyglot hub spawns one driver-host subprocess per top-level export entry; those
            # must run in this same venv (jumpstarter_core + drivers importable), so default the
            # host interpreter to ours (an explicit JMP_DRIVER_HOST_PYTHON still wins).
            os.environ.setdefault("JMP_DRIVER_HOST_PYTHON", sys.executable)

            path = str(config_path)
            try:
                # The hub federates the per-entry hosts by UUID — no in-process factory here;
                # each per-entry host provides its own (jumpstarter.exporter_host).
                exit_kind = await jc.run_exporter_polyglot(path)
            except* Exception as excgroup:
                _handle_exporter_exceptions(excgroup)
            tg.cancel_scope.cancel()

        if received_signal:
            return 128 + received_signal
        # A shutdown signal or an `on_failure: exit` hook is terminal — tell the parent NOT to
        # restart. A clean run_exporter return (Completed) otherwise means a recoverable exit.
        if exit_kind == jc.ExporterExit.SHUTDOWN:
            return _EXPORTER_SHUTDOWN_EXIT_CODE
        return 0

    sys.exit(anyio.run(serve_with_graceful_shutdown))


def _wait_for_child(pid, child_info):
    """Wait for child process, get status from signal handler if reaped."""
    try:
        _, status = os.waitpid(pid, 0)
    except ChildProcessError:
        status = child_info['status']
    return status


def _interpret_child_status(status):
    """Map a child wait() status to the parent's action: ``None`` = restart the child,
    otherwise the process exit code to terminate with."""
    if os.WIFEXITED(status):
        child_exit_code = os.WEXITSTATUS(status)
        if child_exit_code == 0:
            return None  # restart child (recoverable exit/exception)
        if child_exit_code == _EXPORTER_SHUTDOWN_EXIT_CODE:
            return 0  # terminal shutdown (on_failure=exit hook or signal): exit cleanly, no restart
        # Child already encodes signals as 128+N; pass through directly.
        return child_exit_code
    # Child killed by an unhandled signal — terminate.
    child_exit_signal = os.WTERMSIG(status) if os.WIFSIGNALED(status) else 0
    click.echo(f"Child killed by unhandled signal: {child_exit_signal}", err=True)
    return 128 + child_exit_signal


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
    return _interpret_child_status(status)


def _serve_with_exc_handling(config_path):
    max_rapid_failures, rapid_failure_window = _failure_detection(config_path)

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
            _handle_child(config_path)
            sys.exit(1) # should never happen


def _serve_standalone(config_path, bind, passphrase):
    """Serve the exporter on a TCP listener (controller-less), hosting drivers in-process via
    the same FFI host as the controller-mediated path. One-shot — beforeLease → serve →
    afterLease → exit — with no fork/restart supervisor (a standalone exporter is not meant to
    auto-recover; the Rust core handles SIGTERM/SIGINT and the client EndSession)."""

    async def serve():
        import jumpstarter_core as jc

        from jumpstarter.exporter.host import DriverHostFactory

        # Foreign async callbacks run on Rust/tokio worker threads where
        # asyncio.get_running_loop() raises; register this loop with UniFFI so they schedule
        # onto it (mirrors _handle_child).
        set_event_loop = getattr(jc, "uniffi_set_event_loop", None)
        if set_event_loop is None:
            from jumpstarter_core import jumpstarter_core as _jc_mod

            set_event_loop = _jc_mod.uniffi_set_event_loop
        set_event_loop(asyncio.get_running_loop())

        path = str(config_path)
        factory = DriverHostFactory(path)
        await jc.run_exporter_standalone(path, bind, passphrase, factory)

    anyio.run(serve)


@click.command("run")
@click.option("--exporter", "exporter_alias", default=None, help="Alias of an exporter config to run.")
@click.option(
    "--exporter-config",
    "exporter_config",
    default=None,
    help="Path to an exporter config file to run.",
)
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
def run(exporter_alias, exporter_config, listener_bind, tls_insecure, tls_cert, tls_key, passphrase):
    """Run an exporter locally."""
    config_path = _resolve_exporter_config_path(exporter_alias, exporter_config)
    if config_path is None:
        raise click.UsageError("--exporter-config (or --exporter) is required")
    standalone = listener_bind is not None
    if not standalone and (tls_insecure or tls_cert or tls_key or passphrase):
        raise click.UsageError(
            "--tls-grpc-insecure, --tls-cert, --tls-key, and --passphrase require --tls-grpc-listener"
        )
    if not config_path.exists():
        raise click.UsageError(f"exporter config '{config_path}' does not exist")
    if standalone:
        if tls_cert or tls_key or not tls_insecure:
            # The Rust standalone listener serves plaintext h2c; TLS-cert mode is not yet ported.
            raise click.UsageError(
                "--tls-grpc-listener currently requires --tls-grpc-insecure "
                "(TLS-cert listener mode is not yet supported)"
            )
        host, port = _parse_listener_bind(listener_bind)
        return _serve_standalone(config_path, f"{host}:{port}", passphrase)
    return _serve_with_exc_handling(config_path)
