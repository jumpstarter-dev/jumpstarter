from dataclasses import dataclass
from os import environ, getenv

from .portforward import TcpPortforwardAdapter


@dataclass(kw_only=True)
class DbusAdapter(TcpPortforwardAdapter):
    async def __aenter__(self):
        addr = await super().__aenter__()
        match self.client.kind:
            case "system":
                self.varname = "DBUS_SYSTEM_BUS_ADDRESS"
                pass
            case "session":
                self.varname = "DBUS_SESSION_BUS_ADDRESS"
                pass
            case _:
                raise ValueError(f"invalid bus type: {self.client.kind}")
        self.oldenv = getenv(self.varname)
        environ[self.varname] = f"tcp:host={addr[0]},port={addr[1]}"

    async def __aexit__(self, exc_type, exc_value, traceback):
        await super().__aexit__(exc_type, exc_value, traceback)
        if self.oldenv is None:
            del environ[self.varname]
        else:
            environ[self.varname] = self.oldenv
