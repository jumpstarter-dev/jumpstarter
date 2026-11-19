from abc import ABCMeta, abstractmethod


class IosDeviceInterface(metaclass=ABCMeta):
    """One leased iOS device or simulator and its available transports.

    Raw Jumpstarter streams do not carry arguments. Implementations export a
    ``connect_<protocol>`` stream for each protocol advertised by ``info()``.
    Configured device ports are network children named ``port_<port>``.
    """

    driver_type = "iosdevice"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_iosdevice.client.IosDeviceClient"

    @abstractmethod
    async def info(self) -> dict:
        """Return identity, presence, native-first protocols and forward_ports."""

    async def connect_usbmux(self):
        raise NotImplementedError("This target does not support the usbmux protocol")

    async def connect_idb(self):
        raise NotImplementedError("This target does not support the idb protocol")

    async def https_info(self) -> dict:
        """Return public ca_certificate PEM and provider-owned JSON metadata."""
        raise NotImplementedError("This target has no configured HTTPS service")

    async def connect_https(self):
        raise NotImplementedError("This target has no configured HTTPS service")
