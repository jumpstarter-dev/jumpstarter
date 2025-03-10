from contextlib import contextmanager

from jumpstarter_driver_composite.client import CompositeClient
# from jumpstarter_driver_network.adapters import FabricAdapter, NovncAdapter


class CorelliumClient(CompositeClient):
    pass
    # @property
    # def hostname(self) -> str:
    #    return self.call("get_hostname")

    # @contextmanager
    # def shell(self):
    #    with FabricAdapter(
    #        client=self.ssh,
    #        user=self.username,
    #        connect_kwargs={"password": self.password},
    #    ) as conn:
    #        yield conn
