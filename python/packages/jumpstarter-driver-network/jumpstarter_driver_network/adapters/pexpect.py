import socket
from contextlib import contextmanager

from pexpect.socket_pexpect import SocketSpawn

from .portforward import TcpPortforwardAdapter
from jumpstarter.client import DriverClient


class _SocketSpawn(SocketSpawn):
    def read_nonblocking(self, size=1, timeout=-1):
        # SocketSpawn 4.9 bypasses SpawnBase's decoding and read logging.
        data = self._decoder.decode(super().read_nonblocking(size, timeout), final=False)
        self._log(data, "read")
        return data


@contextmanager
def PexpectAdapter(*, client: DriverClient, method: str = "connect"):
    with TcpPortforwardAdapter(client=client, method=method) as addr, socket.create_connection(addr) as sock:
        yield _SocketSpawn(sock)
