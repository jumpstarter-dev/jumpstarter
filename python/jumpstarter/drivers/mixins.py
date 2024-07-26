"""
Mixins for extending DriverClient
"""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from anyio.from_thread import BlockingPortal
from anyio.streams.file import FileReadStream


@dataclass(kw_only=True)
class BlockingStream:
    """
    Raw stream
    """

    stream: Any
    portal: BlockingPortal

    def send(self, data):
        """Send bytes"""
        return self.portal.call(self.stream.send, data)

    def receive(self):
        """Receive bytes"""
        return self.portal.call(self.stream.receive)


class StreamMixin:
    """Streaming"""

    @contextmanager
    def connect(self, method="connect"):
        with self.portal.wrap_async_context_manager(self.stream_async(method)) as stream:
            yield BlockingStream(stream=stream, portal=self.portal)

    @contextmanager
    def portforward(self, listener, method="connect"):
        with self.portal.wrap_async_context_manager(self.portforward_async(method, listener)):
            yield


class ResourceMixin:
    """Resource"""

    @contextmanager
    def local_file(
        self,
        filepath,
    ):
        """
        Share local file with driver

        :param str filepath: path to file
        """
        with self.portal.wrap_async_context_manager(self.portal.call(FileReadStream.from_path, filepath)) as file:
            with self.portal.wrap_async_context_manager(self.resource_async(file)) as uuid:
                yield uuid
