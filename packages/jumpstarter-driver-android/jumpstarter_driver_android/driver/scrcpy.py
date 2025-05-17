from dataclasses import dataclass

from jumpstarter_driver_network.driver import TcpNetwork


@dataclass(kw_only=True)
class Scrcpy(TcpNetwork):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_android.client.ScrcpyClient"

    pass
