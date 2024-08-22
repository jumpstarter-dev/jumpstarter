"""
Base classes for drivers and driver clients
"""

from dataclasses import dataclass

from anyio.from_thread import BlockingPortal

from .core import AsyncDriverClient


@dataclass(kw_only=True)
class DriverClient(AsyncDriverClient):
    """Base class for driver clients

    Client methods can be implemented as regular functions,
    and call the `call` or `streamingcall` helpers internally
    to invoke exported methods on the driver.

    Additional client functionalities such as raw stream
    connections or sharing client-side resources can be added
    by inheriting mixin classes under `jumpstarter.drivers.mixins`
    """

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
