import concurrent.futures._base
import os
import sys
from contextlib import ExitStack
from typing import cast

import click
from anyio import create_task_group, get_cancelled_exc_class, run, to_thread
from anyio.from_thread import BlockingPortal
from click.shell_completion import get_completion_class
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


@async_handle_exceptions
async def _run_cli():
    completing = os.environ.get("_J_COMPLETE", "").endswith("_complete")
    try:
        async with BlockingPortal() as portal:
            with ExitStack() as stack:
                async with env_async(portal, stack) as client:
                    if completing:
                        try:
                            await to_thread.run_sync(lambda: client.cli()(standalone_mode=True))
                        except SystemExit:
                            pass
                    else:
                        result = await to_thread.run_sync(lambda: client.cli()(standalone_mode=False))
                        if isinstance(result, int) and result != 0:
                            raise BaseExceptionGroup("CLI exit", [click.exceptions.Exit(result)])
    except BaseExceptionGroup as eg:
        if exc := find_exception_in_group(eg, EnvironmentVariableNotSetError):
            raise ClickExceptionRed(f"Error: the j command must be used inside a jmp shell: {exc}") from eg
        raise eg


async def j_async():
    try:
        async with create_task_group() as tg:
            tg.start_soon(signal_handler, tg.cancel_scope)

            try:
                await _run_cli()
            finally:
                tg.cancel_scope.cancel()
    except* click.exceptions.Exit as excgroup:
        for exc in leaf_exceptions(excgroup):
            sys.exit(exc.exit_code)
    except* click.ClickException as excgroup:
        for exc in leaf_exceptions(excgroup):
            cast(click.ClickException, exc).show()

        sys.exit(1)
    except* (
        get_cancelled_exc_class(),
        concurrent.futures._base.CancelledError,
    ) as _:
        sys.exit(2)


@click.group()
def _j_placeholder():
    """Jumpstarter driver client (requires jmp shell)"""


def _handle_j_completion(instruction: str):
    """Handle shell completion source generation without entering the async stack.

    Only intercepts *_source instructions (generating the completion script).
    The *_complete instructions (actual tab-completion) must go through the
    async stack where the real driver CLI is built, so they are not handled here.
    """
    if not instruction.endswith("_source"):
        return
    shell = instruction.split("_")[0]
    comp_cls = get_completion_class(shell)
    if comp_cls is None:
        raise SystemExit(1)
    comp = comp_cls(_j_placeholder, {}, "j", "_J_COMPLETE")
    click.echo(comp.source())
    raise SystemExit(0)


def j():
    complete_var = os.environ.get("_J_COMPLETE")
    if complete_var:
        _handle_j_completion(complete_var)
    traceback.install()
    run(j_async)


if __name__ == "__main__":
    j()
