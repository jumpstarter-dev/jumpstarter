"""
Mixins for extending DriverClient
"""

import socket
from contextlib import contextmanager

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
