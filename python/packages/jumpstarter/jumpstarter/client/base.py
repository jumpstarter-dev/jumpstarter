"""
Base classes for drivers and driver clients
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field

from anyio.from_thread import BlockingPortal

from .core import AsyncDriverClient
from jumpstarter.common.importlib import _format_missing_driver_message
from jumpstarter.streams.blocking import BlockingStream


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
        while True:
            try:
                yield self.portal.call(generator.__anext__)
            except StopAsyncIteration:
                break

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
        self.stack.close()

    def __del__(self):
        self.close()


@dataclass(kw_only=True)
class StubDriverClient(DriverClient):
    """Stub client for drivers that are not installed.

    This client is created when a driver client class cannot be imported.
    It provides a placeholder that raises a clear error when the driver
    is actually used.
    """

    def _get_missing_class_path(self) -> str:
        """Get the missing class path from labels."""
        return self.labels["jumpstarter.dev/client"]

    def _raise_missing_error(self):
        """Raise ImportError with installation instructions."""
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
        while False:  # noqa: SIM114
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


def rebind_client(client: DriverClient, cls: type[DriverClient]) -> DriverClient:
    """Reconstruct ``client`` as an instance of ``cls`` (same uuid/session/children/…).

    Generated device wrappers use this to bind each tree node to the codegen-chosen client
    class — a custom client where one is defined, else the generated typed client — regardless
    of what the runtime resolved from the node's ``jumpstarter.dev/client`` label. Notably this
    upgrades a :class:`StubDriverClient` (driver package not installed client-side) into a fully
    working typed client. A client that already satisfies ``cls`` is returned as-is, so a richer
    runtime-resolved subclass is preserved.
    """
    if isinstance(client, cls):
        return client
    return cls(
        uuid=client.uuid,
        labels=client.labels,
        session=client.session,
        portal=client.portal,
        stack=client.stack,
        children=client.children,
        description=client.description,
        methods_description=client.methods_description,
    )


def resolve_root_child(root: DriverClient, name: str) -> DriverClient:
    """Resolve a device tree's first path segment against the client tree root.

    The named export entry is either a child of the root composite (the exporter hub's
    synthesized tree), the root client itself (a config-served single entry), or — for a bare
    single-driver harness ``serve()`` whose root carries no name label and no children — the
    root unambiguously.
    """
    if name in root.children:
        return root.children[name]
    root_name = root.labels.get("jumpstarter.dev/name")
    if root_name == name:
        return root
    if root_name is None and not root.children:
        return root
    raise KeyError(f"driver {name!r} not found under the exporter root")
