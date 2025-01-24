from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass

import jumpstarter_rust
from anyio import create_task_group
from anyio.from_thread import start_blocking_portal

from jumpstarter.common import TemporarySocket


@dataclass(kw_only=True)
class Session(jumpstarter_rust.Session):
    @asynccontextmanager
    async def serve_unix_async(self):
        async with create_task_group() as tg:
            with TemporarySocket() as path:
                tg.start_soon(super().serve_unix, str(path))
                yield path
                tg.cancel_scope.cancel()

    @contextmanager
    def serve_unix(self):
        with start_blocking_portal() as portal:
            with portal.wrap_async_context_manager(self.serve_unix_async()) as path:
                yield path
