from contextlib import contextmanager

from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.adapters import FabricAdapter, NovncAdapter


class QemuClient(CompositeClient):
    @property
    def hostname(self) -> str:
        return self.call("get_hostname")

    @property
    def username(self) -> str:
        return self.call("get_username")

    @property
    def password(self) -> str:
        return self.call("get_password")

    @contextmanager
    def novnc(self):
        with NovncAdapter(client=self.vnc) as url:
            yield url

    @contextmanager
    def shell(self):
        with FabricAdapter(
            client=self.ssh,
            user=self.username,
            connect_kwargs={"password": self.password},
        ) as conn:
            yield conn
