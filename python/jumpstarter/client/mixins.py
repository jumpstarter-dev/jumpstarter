"""
Mixins for extending DriverClient
"""

import socket
from contextlib import contextmanager

from opendal import Operator
from pexpect.fdpexpect import fdspawn

from jumpstarter.client.adapters import PortforwardAdapter


class ExpectMixin:
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
