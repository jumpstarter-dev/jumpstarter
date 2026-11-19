from contextlib import asynccontextmanager, contextmanager, nullcontext
from os import PathLike
from pathlib import Path
from socket import AddressFamily
from tempfile import TemporaryDirectory

from anyio import create_task_group, create_tcp_listener, create_unix_listener
from anyio.abc import SocketAttribute
from xdg_base_dirs import xdg_runtime_dir


@contextmanager
def _collapse_single_exception_group():
    """Re-raise a one-member ExceptionGroup as that member.

    The listeners below yield inside a TaskGroup, and a TaskGroup wraps every
    exception that crosses it, including one raised by the caller's ``with``
    body. That exception is the caller's own, so hand it back as itself. A
    group with several members is a real concurrent failure and stays a group.
    """
    try:
        yield
    except BaseExceptionGroup as group:
        exc: BaseException = group
        while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
            exc = exc.exceptions[0]
        if exc is group:
            raise
        context = exc.__context__
        try:
            raise exc
        finally:
            # raising here chained the group in as __context__; restore the original
            exc.__context__ = context


@contextmanager
def TemporarySocket():
    with TemporaryDirectory(dir=xdg_runtime_dir(), prefix="jumpstarter-") as tempdir:
        yield Path(tempdir) / "socket"


@asynccontextmanager
async def TemporaryUnixListener(handler, path: PathLike | None = None):
    if path is not None:
        cm = nullcontext(path)
    else:
        cm = TemporarySocket()

    with cm as resolved_path, _collapse_single_exception_group():
        async with await create_unix_listener(resolved_path) as listener, create_task_group() as tg:
            tg.start_soon(listener.serve, handler, tg)
            try:
                yield resolved_path
            finally:
                tg.cancel_scope.cancel()


@asynccontextmanager
async def TemporaryTcpListener(
    handler, local_host="127.0.0.1", local_port=0, family=AddressFamily.AF_UNSPEC, backlog=65536, reuse_port=True
):
    with _collapse_single_exception_group():
        async with await create_tcp_listener(
            local_host=local_host,
            local_port=local_port,
            family=family,
            backlog=backlog,
            reuse_port=reuse_port,
        ) as listener, create_task_group() as tg:
            tg.start_soon(listener.serve, handler, tg)
            try:
                yield listener.extra(SocketAttribute.local_address)
            finally:
                tg.cancel_scope.cancel()
