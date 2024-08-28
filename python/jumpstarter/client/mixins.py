"""
Mixins for extending DriverClient
"""

import socket
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from anyio.from_thread import BlockingPortal
from opendal import Operator
from pexpect.fdpexpect import fdspawn

from jumpstarter.client.adapters import PortforwardAdapter


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


class ExpectMixin(StreamMixin):
    @contextmanager
    def expect(self):
        """
        Connect to the driver and returns a pexpect instance

        Useful for interacting with serial consoles.
        """
        with PortforwardAdapter(client=self) as addr:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect(addr)
                yield fdspawn(s)


class ResourceMixin:
    """Resource"""

    @contextmanager
    def file(self, operator: Operator, path: str):
        with self.portal.wrap_async_context_manager(self.file_async(operator.to_async_operator(), path)) as uuid:
            yield uuid
