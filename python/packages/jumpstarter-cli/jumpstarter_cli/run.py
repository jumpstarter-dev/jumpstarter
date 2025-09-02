import logging
import os
import signal
import sys

import anyio
import click
from anyio import create_task_group, open_signal_receiver
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions

logger = logging.getLogger(__name__)


def _handle_child(config):
    """Handle child process with graceful shutdown."""
    async def serve_with_graceful_shutdown():
        received_signal = 0
        signal_handled = False
        exporter = None

        async def signal_handler():
            nonlocal received_signal, signal_handled

            with open_signal_receiver(signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT) as signals:
                async for sig in signals:
                    if signal_handled:
                        continue  # Ignore duplicate signals
                    received_signal = sig
                    logger.info("CHILD: Received %d (%s)", received_signal, signal.Signals(received_signal).name)
                    if exporter:
                        # Terminate exporter. SIGHUP waits until current lease is let go. Later SIGTERM still overrides
                        if received_signal != signal.SIGHUP:
                            signal_handled = True
                        exporter.stop(wait_for_lease_exit=received_signal == signal.SIGHUP)

                # Start signal handler first, then create exporter
        async with create_task_group() as signal_tg:

            # Start signal handler immediately
            signal_tg.start_soon(signal_handler)

            # Create exporter and run it
            async with config.create_exporter() as exporter:
                try:
                    await exporter.serve()
                except* Exception as excgroup:
                    from jumpstarter_cli_common.exceptions import leaf_exceptions
                    for exc in leaf_exceptions(excgroup):
                        if not isinstance(exc, anyio.get_cancelled_exc_class()):
                            click.echo(
                                f"Exception while serving on the exporter: {type(exc).__name__}: {exc}",
                                err=True,
                            )

            # Cancel the signal handler after exporter completes
            signal_tg.cancel_scope.cancel()

        # Return signal number if received, otherwise 0 for immediate restart
        return received_signal if received_signal else 0

    sys.exit(anyio.run(serve_with_graceful_shutdown))


def _handle_parent(pid):
    """Handle parent process waiting for child and signal forwarding."""
    def parent_signal_handler(signum, _):
        logger.info("PARENT: Received %d (%s), forwarding to child PID %d", signum, signal.Signals(signum).name, pid)
        if pid and pid > 0:
            try:
                os.kill(pid, signum)
            except ProcessLookupError:
                pass

    # Set up signal handlers after fork
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(sig, parent_signal_handler)

    _, status = os.waitpid(pid, 0)
    if os.WIFEXITED(status):
        # Interpret child exit code
        child_exit_code = os.WEXITSTATUS(status)
        if child_exit_code == 0:
            return None  # restart child (unexpected exit/exception)
        else:
            # Child indicates termination (signal number)
            return 128 + child_exit_code  # Return standard Unix exit code
    else:
        # Child killed by unhandled signal - terminate
        child_exit_signal = os.WTERMSIG(status) if os.WIFSIGNALED(status) else 0
        click.echo(f"Child killed by unhandled signal: {child_exit_signal}", err=True)
        return 128 + child_exit_signal


def _serve_with_exc_handling(config):
    while True:
        pid = os.fork()

        if pid > 0:
            if (exit_code := _handle_parent(pid)) is not None:
                return exit_code
        else:
            _handle_child(config)
            sys.exit(1) # should never happen


@click.command("run")
@opt_config(client=False)
@handle_exceptions
def run(config):
    """Run an exporter locally."""
    return _serve_with_exc_handling(config)
