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
    stream: Any
    portal: BlockingPortal

    def send(self, data):
        return self.portal.call(self.stream.send, data)

    def receive(self):
        return self.portal.call(self.stream.receive)


class StreamMixin:
    @contextmanager
    def connect(self):
        with self.portal.wrap_async_context_manager(self.stream_async()) as stream:
            yield BlockingStream(stream=stream, portal=self.portal)

    @contextmanager
    def portforward(self, listener):
        with self.portal.wrap_async_context_manager(self.portforward_async(listener)):
            yield


class ResourceMixin:
    @contextmanager
    def local_file(
        self,
        filepath,
    ):
        with self.portal.wrap_async_context_manager(self.portal.call(FileReadStream.from_path, filepath)) as file:
            with self.portal.wrap_async_context_manager(self.resource_async(file)) as uuid:
                yield uuid
