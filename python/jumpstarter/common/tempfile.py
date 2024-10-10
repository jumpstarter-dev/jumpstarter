from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from anyio import create_task_group, create_unix_listener


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
