import concurrent.futures._base
import os
import sys
from contextlib import AsyncExitStack, ExitStack
from typing import cast

import click
from anyio import create_task_group, get_cancelled_exc_class, run, to_thread
from anyio.from_thread import BlockingPortal
from click.exceptions import Exit as ClickExit
from click.shell_completion import CompletionItem
from jumpstarter_cli_common.exceptions import (
    ClickExceptionRed,
    async_handle_exceptions,
    find_exception_in_group,
    leaf_exceptions,
)
from jumpstarter_cli_common.signal import signal_handler
from rich import traceback

from jumpstarter.common.exceptions import EnvironmentVariableNotSetError
from jumpstarter.config.env import JMP_LEASE, JMP_LEASE_MEMBERS, JUMPSTARTER_HOST
from jumpstarter.utils.env import env_async, env_member_async, lease_members_from_env


class RolesGroup(click.Group):
    """The `j` command of a shell on a lease with members: each role is a command.

    The client of a role is connected only when that role is used, so `j`
    alone, and completion of the role names, need no connection.
    """

    def __init__(self, members: dict[str, dict | None], connect, **kwargs):
        super().__init__(**kwargs)
        self.members = members
        self._connect = connect

    def list_commands(self, ctx):
        return list(self.members)

    def get_command(self, ctx, cmd_name):
        if cmd_name not in self.members:
            return None
        if self.members[cmd_name] is None:
            if ctx.resilient_parsing:
                return None
            raise ClickExceptionRed(
                f"Error: the role '{cmd_name}' has no exporter in this lease: it is optional and was left out"
            )
        return self._connect(cmd_name).cli()

    def shell_complete(self, ctx, incomplete):
        # The default asks each command for its help, which would connect to every role.
        return [
            CompletionItem(role, help=f"exporter {entry.get('exporter', '')}")
            for role, entry in self.members.items()
            if entry is not None and role.startswith(incomplete)
        ]

    def format_commands(self, ctx, formatter):
        rows = [
            (role, f"exporter {entry.get('exporter', '')}" if entry is not None else "(no exporter)")
            for role, entry in self.members.items()
        ]
        if rows:
            with formatter.section("Roles"):
                formatter.write_dl(rows)


def roles_cli(members: dict[str, dict | None], connect) -> RolesGroup:
    lease = os.environ.get(JMP_LEASE, "")
    return RolesGroup(
        members,
        connect,
        name="j",
        help=f"The members of the lease{' ' + lease if lease else ''}. Use 'j ROLE --help' for the drivers of a role.",
    )


async def _members_cli():
    """Run `j ROLE DRIVER ...` against the role's exporter."""
    members = lease_members_from_env()
    async with BlockingPortal() as portal:
        with ExitStack() as stack:
            async with AsyncExitStack() as clients:

                def connect(role: str):
                    # Called from the worker thread that runs the command line.
                    return portal.call(clients.enter_async_context, env_member_async(role, portal, stack))

                group = roles_cli(members, connect)
                return await to_thread.run_sync(lambda: group(standalone_mode=False))


def _in_members_shell() -> bool:
    return not os.environ.get(JUMPSTARTER_HOST) and bool(os.environ.get(JMP_LEASE_MEMBERS))


async def _client_cli():
    async with BlockingPortal() as portal:
        with ExitStack() as stack:
            async with env_async(portal, stack) as client:
                return await to_thread.run_sync(lambda: client.cli()(standalone_mode=False))


async def j_async():
    @async_handle_exceptions
    async def cli():
        try:
            result = await (_members_cli() if _in_members_shell() else _client_cli())
            if isinstance(result, int) and result != 0:
                raise BaseExceptionGroup("CLI exit", [ClickExit(result)])
        except BaseExceptionGroup as eg:
            # Handle exceptions wrapped in ExceptionGroup (e.g., from task groups)
            if exc := find_exception_in_group(eg, EnvironmentVariableNotSetError):
                raise ClickExceptionRed(f"Error: the j command must be used inside a jmp shell: {exc}") from eg
            raise
    try:
        async with create_task_group() as tg:
            tg.start_soon(signal_handler, tg.cancel_scope)

            try:
                await cli()
            finally:
                tg.cancel_scope.cancel()
    except* ClickExit as excgroup:
        for exc in leaf_exceptions(excgroup):
            sys.exit(cast(ClickExit, exc).exit_code)
    except* click.ClickException as excgroup:
        for exc in leaf_exceptions(excgroup):
            cast(click.ClickException, exc).show()

        sys.exit(1)
    except* (
        get_cancelled_exc_class(),
        concurrent.futures._base.CancelledError,
    ) as _:
        sys.exit(2)


def j():
    traceback.install()
    run(j_async)


if __name__ == "__main__":
    j()
