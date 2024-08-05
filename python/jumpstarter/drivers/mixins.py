"""
Mixins for extending DriverClient
"""

import socket
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from anyio import create_unix_listener
from anyio.from_thread import BlockingPortal
from anyio.streams.file import FileReadStream
from opendal import Operator
from pexpect.fdpexpect import fdspawn


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


class ExpectMixin(StreamMixin):
    @contextmanager
    def expect(self):
        """
        Connect to the driver and returns a pexpect instance

        Useful for interacting with serial consoles.
        """
        with TemporaryDirectory() as tempdir:
            socketpath = Path(tempdir) / "socket"

            listener = self.portal.call(create_unix_listener, socketpath)

            with self.portforward(listener):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.connect(str(socketpath))
                    yield fdspawn(s)


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

    @contextmanager
    def file(self, operator: Operator, path: str):
        with self.portal.wrap_async_context_manager(self.file_async(operator.to_async_operator(), path)) as uuid:
            yield uuid
