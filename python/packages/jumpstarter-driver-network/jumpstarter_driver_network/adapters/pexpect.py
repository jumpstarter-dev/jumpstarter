import socket
from dataclasses import dataclass

from pexpect.fdpexpect import fdspawn

from .portforward import PortforwardAdapter


@dataclass(kw_only=True)
class PexpectAdapter(PortforwardAdapter):
    async def __aenter__(self):
        addr = await super().__aenter__()

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.connect(addr)

        return fdspawn(self.socket)

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.socket.close()

        await super().__aexit__(exc_type, exc_value, traceback)
