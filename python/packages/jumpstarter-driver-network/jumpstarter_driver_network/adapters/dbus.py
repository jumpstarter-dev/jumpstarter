from contextlib import contextmanager
from os import environ, getenv

from .portforward import TcpPortforwardAdapter
from jumpstarter.client import DriverClient


@contextmanager
def DbusAdapter(*, client: DriverClient):
    match client.kind:
        case "system":
            varname = "DBUS_SYSTEM_BUS_ADDRESS"
            pass
        case "session":
            varname = "DBUS_SESSION_BUS_ADDRESS"
            pass
        case _:
            raise ValueError(f"invalid bus type: {client.kind}")

    oldenv = getenv(varname)

    with TcpPortforwardAdapter(client=client) as addr:
        environ[varname] = f"tcp:host={addr[0]},port={addr[1]}"

        try:
            yield
        finally:
            if oldenv is None:
                del environ[varname]
            else:
                environ[varname] = oldenv
