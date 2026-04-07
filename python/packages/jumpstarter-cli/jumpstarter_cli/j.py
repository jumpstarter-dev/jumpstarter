import concurrent.futures._base
import os
import sys
from contextlib import ExitStack
from typing import cast

import click
from anyio import create_task_group, get_cancelled_exc_class, run, to_thread
from anyio.from_thread import BlockingPortal
from click.exceptions import Exit as ClickExit
from jumpstarter_cli_common.completion import make_completion_command
from jumpstarter_cli_common.exceptions import (
    ClickExceptionRed,
    async_handle_exceptions,
    find_exception_in_group,
    leaf_exceptions,
)
from jumpstarter_cli_common.signal import signal_handler
from rich import traceback

from jumpstarter.common.exceptions import EnvironmentVariableNotSetError
from jumpstarter.utils.env import env_async

j_completion = make_completion_command(lambda: click.Group("j"), "j", "_J_COMPLETE")


async def _j_shell_complete():
    async with BlockingPortal() as portal:
        with ExitStack() as stack:
            async with env_async(portal, stack) as client:

                def _run_completion():
                    try:
                        client.cli()(standalone_mode=False)
                    except SystemExit:
                        pass

                await to_thread.run_sync(_run_completion)


async def j_async():
    @async_handle_exceptions
    async def cli():
        try:
            async with BlockingPortal() as portal:
                with ExitStack() as stack:
                    async with env_async(portal, stack) as client:
                        result = await to_thread.run_sync(lambda: client.cli()(standalone_mode=False))
                        if isinstance(result, int) and result != 0:
                            raise BaseExceptionGroup("CLI exit", [ClickExit(result)])
        except BaseExceptionGroup as eg:
            # Handle exceptions wrapped in ExceptionGroup (e.g., from task groups)
            if exc := find_exception_in_group(eg, EnvironmentVariableNotSetError):
                raise ClickExceptionRed(f"Error: the j command must be used inside a jmp shell: {exc}") from eg
            raise eg
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
    if len(sys.argv) >= 2 and sys.argv[1] == "completion":
        j_completion(args=sys.argv[2:])
        return
    if "_J_COMPLETE" in os.environ:
        run(_j_shell_complete)
        return
    run(j_async)


if __name__ == "__main__":
    j()
