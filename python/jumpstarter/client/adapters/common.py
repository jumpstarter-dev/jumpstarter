from contextlib import AbstractAsyncContextManager, AbstractContextManager
from dataclasses import dataclass

from jumpstarter.client import DriverClient


@dataclass(kw_only=True)
class ClientAdapter(AbstractContextManager, AbstractAsyncContextManager):
    client: DriverClient

    async def __aenter__(self):
        pass

    async def __aexit__(self, exc_type, exc_value, traceback):
        pass

    def __enter__(self):
        self.manager = self.client.portal.wrap_async_context_manager(self)

        return self.manager.__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        self.manager.__exit__(exc_type, exc_value, traceback)
