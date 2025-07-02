import concurrent.futures._base
import sys
from contextlib import ExitStack
from typing import cast

import click
from anyio import create_task_group, get_cancelled_exc_class, run, to_thread
from anyio.from_thread import BlockingPortal
from jumpstarter_cli_common.exceptions import async_handle_exceptions, leaf_exceptions
from jumpstarter_cli_common.signal import signal_handler
from rich import traceback

from jumpstarter.utils.env import env_async


async def j_async():
    @async_handle_exceptions
    async def cli():
        async with BlockingPortal() as portal:
            with ExitStack() as stack:
                async with env_async(portal, stack) as client:
                    async with client.log_stream_async():
                        await to_thread.run_sync(lambda: client.cli()(standalone_mode=False))

    try:
        async with create_task_group() as tg:
            tg.start_soon(signal_handler, tg.cancel_scope)

            try:
                await cli()
            finally:
                tg.cancel_scope.cancel()

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
