from contextlib import asynccontextmanager, contextmanager, nullcontext
from os import PathLike
from pathlib import Path
from socket import AddressFamily
from tempfile import TemporaryDirectory

from anyio import create_task_group, create_tcp_listener, create_unix_listener
from anyio.abc import SocketAttribute
from xdg_base_dirs import xdg_runtime_dir


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

    with cm as path:
        async with await create_unix_listener(path) as listener:
            async with create_task_group() as tg:
                tg.start_soon(listener.serve, handler, tg)
                try:
                    yield path
                finally:
                    tg.cancel_scope.cancel()


@asynccontextmanager
async def TemporaryTcpListener(
    handler, local_host="127.0.0.1", local_port=0, family=AddressFamily.AF_UNSPEC, backlog=65536, reuse_port=True
):
    async with await create_tcp_listener(
        local_host=local_host,
        local_port=local_port,
        family=family,
        backlog=backlog,
        reuse_port=reuse_port,
    ) as listener:
        async with create_task_group() as tg:
            tg.start_soon(listener.serve, handler, tg)
            try:
                yield listener.extra(SocketAttribute.local_address)
            finally:
                tg.cancel_scope.cancel()
