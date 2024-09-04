"""
Base classes for drivers and driver clients
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import field

from anyio.from_thread import BlockingPortal
from pydantic import ConfigDict
from pydantic.dataclasses import dataclass

from jumpstarter.streams import BlockingStream

from .core import AsyncDriverClient


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class DriverClient(AsyncDriverClient):
    """Base class for driver clients

    Client methods can be implemented as regular functions,
    and call the `call` or `streamingcall` helpers internally
    to invoke exported methods on the driver.

    Additional client functionalities such as raw stream
    connections or sharing client-side resources can be added
    by inheriting mixin classes under `jumpstarter.drivers.mixins`
    """

    children: dict[str, DriverClient] = field(default_factory=dict)

    portal: BlockingPortal

    def call(self, method, *args):
        """
        Invoke driver call

        :param str method: method name of driver call
        :param list[Any] args: arguments for driver call

        :return: driver call result
        :rtype: Any
        """
        return self.portal.call(self.call_async, method, *args)

    def streamingcall(self, method, *args):
        """
        Invoke streaming driver call

        :param str method: method name of streaming driver call
        :param list[Any] args: arguments for streaming driver call

        :return: streaming driver call result
        :rtype: Generator[Any, None, None]
        """
        generator = self.portal.call(self.streamingcall_async, method, *args)
        while True:
            try:
                yield self.portal.call(generator.__anext__)
            except StopAsyncIteration:
                break

    @contextmanager
    def stream(self, method="connect"):
        with self.portal.wrap_async_context_manager(self.stream_async(method)) as stream:
            yield BlockingStream(stream=stream, portal=self.portal)
