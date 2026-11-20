"""
Base classes for drivers and driver clients
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import field
from types import AsyncGeneratorType

import anyio
from anyio.from_thread import BlockingPortal
from pydantic import ConfigDict
from pydantic.dataclasses import dataclass

from .core import AsyncDriverClient
from jumpstarter.common.importlib import _format_missing_driver_message
from jumpstarter.streams.blocking import BlockingStream

_CANCELLED_READ_GRACE = 5.0
"""Seconds a cancelled streaming read gets to unwind before the stream is left to it"""


async def _aclose_stream(generator: AsyncGeneratorType) -> None:
    """Close a streaming call's async generator from the portal.

    A read that was cancelled from the caller's thread may still be unwinding,
    and aclose() on a running generator raises. Wait for the read to finish:
    its cancellation already closed the generator, and aclose() is then a
    no-op. Should it outlast the grace period, the cancelled read finishes the
    generator itself, so there is nothing left to close.
    """
    with anyio.move_on_after(_CANCELLED_READ_GRACE):
        while generator.ag_running:
            await anyio.sleep(0.01)
    if not generator.ag_running:
        await generator.aclose()


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
    stack: ExitStack

    description: str | None = None
    """Driver description from GetReport(), used for CLI help text"""

    methods_description: dict[str, str] = field(default_factory=dict)
    """Map of method names to their help descriptions from GetReport()"""

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
        try:
            while True:
                future = self.portal.start_task_soon(generator.__anext__)
                try:
                    item = future.result()
                except StopAsyncIteration:
                    break
                except BaseException:
                    # Interrupted while waiting for an item (a Robot Framework
                    # timeout raises in this thread): the read is still pending
                    # in the portal. Cancelling it cancels the driver-side stream.
                    # A read that failed on its own is done, and cancel() is a no-op.
                    future.cancel()
                    raise
                yield item
        finally:
            # A caller that stops early (islice, break) closes this generator;
            # close the async one too, so the driver-side stream is cancelled
            # instead of being left running until the session ends. A portal
            # that has already stopped took the stream down with it.
            try:
                self.portal.call(_aclose_stream, generator)
            except RuntimeError as exc:
                if "portal is not running" not in str(exc):
                    raise

    @contextmanager
    def stream(self, method="connect"):
        """
        Open a blocking stream session with a context manager.

        :param str method: method name of streaming driver call

        :return: blocking stream session object context manager.
        """

        with self.portal.wrap_async_context_manager(self.stream_async(method)) as stream:
            yield BlockingStream(stream=stream, portal=self.portal)

    @contextmanager
    def log_stream(self):
        with self.portal.wrap_async_context_manager(self.log_stream_async()):
            yield

    def open_stream(self) -> BlockingStream:
        """
        Open a blocking stream session without a context manager.

        :return: blocking stream session object.
        :rtype: BlockingStream
        """
        return self.stack.enter_context(self.stream())

    def close(self):
        """
        Close the open stream session without a context manager.
        """
        if hasattr(self, "stack"):
            self.stack.close()

    def __del__(self):
        self.close()


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class StubDriverClient(DriverClient):
    """Stub client for drivers that are not installed.

    This client is created when a driver client class cannot be imported.
    It provides a placeholder that raises a clear error when the driver
    is actually used.
    """

    # Why the client class was unavailable. Without it the stub can only guess,
    # and reports an allow list rejection as an uninstalled package.
    missing_reason: str | None = None

    def _get_missing_class_path(self) -> str:
        """Get the missing class path from labels."""
        return self.labels["jumpstarter.dev/client"]

    def _raise_missing_error(self):
        """Raise ImportError explaining why the driver client is unavailable."""
        if self.missing_reason:
            raise ImportError(self.missing_reason)
        class_path = self._get_missing_class_path()
        message = _format_missing_driver_message(class_path)
        raise ImportError(message)

    def call(self, method, *args):
        """Invoke driver call - raises ImportError since driver is not installed."""
        self._raise_missing_error()

    def streamingcall(self, method, *args):
        """Invoke streaming driver call - raises ImportError since driver is not installed."""
        self._raise_missing_error()
        # Unreachable yield to make this a generator function for type checking
        while False:
            yield

    @contextmanager
    def stream(self, method="connect"):
        """Open a stream - raises ImportError since driver is not installed."""
        self._raise_missing_error()
        yield

    @contextmanager
    def log_stream(self):
        """Open a log stream - raises ImportError since driver is not installed."""
        self._raise_missing_error()
        yield

    def __getattr__(self, name):
        """Catch any attribute access and raise the missing driver error.

        This ensures that calls like .on(), .off(), .write() etc. on stub clients
        raise a helpful ImportError instead of AttributeError.
        """
        self._raise_missing_error()


def resolve_root_child(root: DriverClient, name: str) -> DriverClient:
    """Resolve the first segment of a driver tree path against the client root.

    The named export entry is either a child of the root composite (the
    exporter's tree), the root client itself (a config-served single entry),
    or, for a bare single-driver ``serve()`` whose root carries no name label
    and no children, the root unambiguously.
    """
    if name in root.children:
        return root.children[name]
    root_name = root.labels.get("jumpstarter.dev/name")
    if root_name == name:
        return root
    if root_name is None and not root.children:
        return root
    raise KeyError(f"driver {name!r} not found under the exporter root")
