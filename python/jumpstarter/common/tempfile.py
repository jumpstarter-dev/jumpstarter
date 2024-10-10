from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from socket import AddressFamily
from tempfile import TemporaryDirectory

from anyio import create_task_group, create_tcp_listener, create_unix_listener
from anyio.abc import SocketAttribute


@contextmanager
def TemporarySocket():
    with TemporaryDirectory(prefix="jumpstarter-") as tempdir:
        yield Path(tempdir) / "socket"


@asynccontextmanager
async def TemporaryUnixListener(handler):
    with TemporarySocket() as path:
        async with await create_unix_listener(path) as listener:
            async with create_task_group() as tg:
                tg.start_soon(listener.serve, handler, tg)
                yield path
                tg.cancel_scope.cancel()


@asynccontextmanager
async def TemporaryTcpListener(
    handler, local_host=None, local_port=0, family=AddressFamily.AF_UNSPEC, backlog=65536, reuse_port=False
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
            yield listener.extra(SocketAttribute.local_address)
            tg.cancel_scope.cancel()
