import logging
import sys
from datetime import timedelta

import anyio
import click
from anyio import create_task_group, get_cancelled_exc_class
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions_with_reauthentication
from jumpstarter_cli_common.oidc import (
    TOKEN_EXPIRY_WARNING_SECONDS,
    Config,
    decode_jwt_issuer,
    format_duration,
    get_token_remaining_seconds,
)
from jumpstarter_cli_common.signal import signal_handler

from .common import opt_acquisition_timeout, opt_duration_partial, opt_selector
from .login import relogin_client
from jumpstarter.common.utils import launch_shell
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.exporter import ExporterConfigV1Alpha1

logger = logging.getLogger(__name__)

# Refresh token when less than this many seconds remain
_TOKEN_REFRESH_THRESHOLD_SECONDS = 120


def _warn_about_expired_token(lease_name: str, selector: str) -> None:
    """Warn user that lease won't be cleaned up due to expired token."""
    click.echo(click.style("\nToken expired - lease cleanup will fail.", fg="yellow", bold=True))
    click.echo(click.style(f"Lease '{lease_name}' will remain active.", fg="yellow"))
    click.echo(click.style(f"To reconnect: JMP_LEASE={lease_name} jmp shell", fg="cyan"))


async def _update_lease_channel(config, lease) -> None:
    """Update the lease's gRPC channel with the current config credentials."""
    if lease is not None:
        new_channel = await config.channel()
        lease.refresh_channel(new_channel)


async def _try_refresh_token(config, lease) -> bool:
    """Attempt to refresh the token and update the lease channel.

    Returns True if refresh succeeded, False otherwise.
    """
    refresh_token = getattr(config, "refresh_token", None)
    if not refresh_token:
        return False

    old_token = config.token
    old_refresh_token = config.refresh_token
    try:
        issuer = decode_jwt_issuer(config.token)
        oidc = Config(
            issuer=issuer,
            client_id="jumpstarter-cli",
            offline_access=True,
        )

        tokens = await oidc.refresh_token_grant(refresh_token)
        config.token = tokens["access_token"]
        new_refresh_token = tokens.get("refresh_token")
        if new_refresh_token is not None:
            config.refresh_token = new_refresh_token

        # Update the lease channel first (critical for the running session)
        await _update_lease_channel(config, lease)

        # Persist to disk (best-effort, uses original config path)
        try:
            ClientConfigV1Alpha1.save(config, path=config.path)
        except Exception as e:
            logger.warning("Failed to save refreshed token to disk: %s", e)

        return True
    except Exception as e:
        # Restore old token so the monitor doesn't think we succeeded
        config.token = old_token
        config.refresh_token = old_refresh_token
        logger.debug("Token refresh failed: %s", e)
        return False


async def _try_reload_token_from_disk(config, lease) -> bool:
    """Check if the config on disk has a newer/valid token (e.g. from 'jmp login').

    If a valid token is found on disk, updates the in-memory config and lease channel.
    Returns True if a valid token was loaded, False otherwise.
    """
    config_path = getattr(config, "path", None)
    if not config_path:
        return False

    old_token = config.token
    old_refresh_token = config.refresh_token
    try:
        disk_config = ClientConfigV1Alpha1.from_file(config_path)
        disk_token = getattr(disk_config, "token", None)
        if not disk_token or disk_token == config.token:
            return False

        # Check if the token on disk is actually valid
        disk_remaining = get_token_remaining_seconds(disk_token)
        if disk_remaining is None or disk_remaining <= 0:
            return False

        # Token on disk is valid and different - use it
        config.token = disk_token
        disk_refresh = getattr(disk_config, "refresh_token", None)
        if disk_refresh is not None:
            config.refresh_token = disk_refresh

        # Update the lease channel (critical for the running session)
        await _update_lease_channel(config, lease)

        return True
    except Exception as e:
        config.token = old_token
        config.refresh_token = old_refresh_token
        logger.debug("Failed to reload token from disk: %s", e)
        return False


async def _attempt_token_recovery(config, lease, remaining) -> str | None:
    """Try all available methods to recover a valid token.

    Attempts OIDC refresh first, then falls back to reloading from disk
    (e.g. if user ran 'jmp login' from the shell).

    Returns a message describing the recovery method, or None if all failed.
    """
    if await _try_refresh_token(config, lease):
        return "Token refreshed automatically."
    if await _try_reload_token_from_disk(config, lease):
        return "Token reloaded from login."
    return None


def _warn_refresh_failed(remaining: float) -> None:
    """Warn the user that token refresh failed."""
    if remaining > 0:
        duration = format_duration(remaining)
        click.echo(
            click.style(
                f"\nToken expires in {duration} and auto-refresh failed. "
                "Run 'jmp login' from this shell to refresh manually.",
                fg="yellow",
                bold=True,
            )
        )
    else:
        click.echo(
            click.style(
                "\nToken expired and auto-refresh failed. "
                "New commands will fail until you run 'jmp login' from this shell.",
                fg="red",
                bold=True,
            )
        )


async def _monitor_token_expiry(config, lease, cancel_scope) -> None:
    """Monitor token expiry, auto-refresh when possible, warn user otherwise.

    this monitor:
    1. Proactively refreshes the token before it expires using the refresh token
    2. Updates the lease's gRPC channel with new credentials
    3. If refresh fails, periodically checks the config on disk for a token
       refreshed externally (e.g. via 'jmp login' from within the shell)
    4. Never cancels the scope - the shell stays alive regardless
    """
    token = getattr(config, "token", None)
    if not token:
        return

    warned_expiry = False
    warned_refresh_failed = False
    while not cancel_scope.cancel_called:
        try:
            # Re-read config.token each iteration since it may have been refreshed
            remaining = get_token_remaining_seconds(config.token)
            if remaining is None:
                return

            # Try to refresh proactively before the token expires
            if remaining <= _TOKEN_REFRESH_THRESHOLD_SECONDS:
                recovery_msg = await _attempt_token_recovery(config, lease, remaining)
                if recovery_msg:
                    click.echo(click.style(f"\n{recovery_msg}", fg="green"))
                    warned_expiry = False
                    warned_refresh_failed = False
                elif not warned_refresh_failed:
                    _warn_refresh_failed(remaining)
                    warned_refresh_failed = True

            elif remaining <= TOKEN_EXPIRY_WARNING_SECONDS and not warned_expiry:
                duration = format_duration(remaining)
                click.echo(
                    click.style(
                        f"\nToken expires in {duration}. Will attempt auto-refresh.",
                        fg="yellow",
                        bold=True,
                    )
                )
                warned_expiry = True

            # Check more frequently as we approach expiry
            if remaining <= _TOKEN_REFRESH_THRESHOLD_SECONDS:
                await anyio.sleep(5)
            else:
                await anyio.sleep(30)
        except Exception:
            return


def _run_shell_with_lease(lease, exporter_logs, config, command):
    """Run shell with lease context managers."""

    def launch_remote_shell(path: str) -> int:
        return launch_shell(
            path,
            lease.exporter_name,
            config.drivers.allow,
            config.drivers.unsafe,
            config.shell.use_profiles,
            command=command,
            lease=lease,
        )

    with lease.serve_unix() as path:
        with lease.monitor():
            if exporter_logs:
                with lease.connect() as client:
                    with client.log_stream():
                        return launch_remote_shell(path)
            else:
                return launch_remote_shell(path)


async def _shell_with_signal_handling(
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
            from jumpstarter.common.exceptions import ConnectionError
            raise ConnectionError("token is expired")

    async with create_task_group() as tg:
        tg.start_soon(signal_handler, tg.cancel_scope)

        try:
            try:
                async with anyio.from_thread.BlockingPortal() as portal:
                    async with config.lease_async(selector, lease_name, duration, portal, acquisition_timeout) as lease:
                        lease_used = lease

                        # Start token monitoring only once we're in the shell
                        tg.start_soon(_monitor_token_expiry, config, lease, tg.cancel_scope)

                        exit_code = await anyio.to_thread.run_sync(
                            _run_shell_with_lease, lease, exporter_logs, config, command
                        )
            except BaseExceptionGroup as eg:
                for exc in eg.exceptions:
                    if isinstance(exc, TimeoutError):
                        raise exc from None
                raise
            except cancelled_exc_class:
                # Check if cancellation was due to token expiry
                token = getattr(config, "token", None)
                if lease_used and token:
                    remaining = get_token_remaining_seconds(token)
                    if remaining is not None and remaining <= 0:
                        _warn_about_expired_token(lease_used.name, selector)
                        return 3  # Exit code for token expiry
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
