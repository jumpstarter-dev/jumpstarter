import concurrent.futures._base
import sys
from contextlib import ExitStack
from typing import cast

import click
from anyio import create_task_group, get_cancelled_exc_class, run, to_thread
from anyio.from_thread import BlockingPortal
from click.exceptions import Exit as ClickExit

from jumpstarter_cli._errors import (
    ClickExceptionRed,
    async_handle_exceptions,
    find_exception_in_group,
    leaf_exceptions,
)
from jumpstarter_cli._signal import signal_handler

from jumpstarter.common.exceptions import EnvironmentVariableNotSetError
from jumpstarter.utils.env import env_async


async def _run_introspect(argv):
    """``j introspect <drivers|explore|driver-methods> [args...]`` — build the client from
    JUMPSTARTER_HOST and emit machine-readable JSON to stdout (consumed by the Rust MCP
    server's explore/drivers/driver_methods tools)."""
    import json

    from jumpstarter_cli.introspect import dispatch

    try:
        async with BlockingPortal() as portal:
            with ExitStack() as stack:
                async with env_async(portal, stack) as client:
                    result = await to_thread.run_sync(lambda: dispatch(client, argv))
    except BaseException as exc:  # noqa: BLE001 — always emit a JSON error, never a traceback
        msg = str(exc)
        if isinstance(exc, BaseExceptionGroup):
            leaves = leaf_exceptions(exc)
            msg = "; ".join(str(e) for e in leaves) if leaves else msg
        result = {"error": msg}
    print(json.dumps(result, indent=2, default=str))


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
    # `j introspect ...` is a machine-readable side channel (JSON to stdout) used by the Rust
    # MCP server; handle it before the normal driver-client CLI passthrough.
    if len(sys.argv) >= 2 and sys.argv[1] == "introspect":
        run(_run_introspect, sys.argv[2:])
        return
    run(j_async)


if __name__ == "__main__":
    j()
