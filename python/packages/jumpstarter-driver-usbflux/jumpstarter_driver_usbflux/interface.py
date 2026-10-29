from abc import ABCMeta, abstractmethod


class UsbFluxInterface(metaclass=ABCMeta):
    """One device on a usbmux daemon, reachable through a lease-scoped usbmux endpoint.

    Raw Jumpstarter streams do not carry arguments. Implementations export
    ``connect_usbmux``, a stream speaking the usbmux plist protocol for exactly
    the selected device; tools attach to it directly or through usbfluxd.
    """

    driver_type = "usbflux"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_usbflux.client.UsbFluxClient"

    @abstractmethod
    async def info(self) -> dict:
        """Return identity, presence and the protocols available for this device."""

    @abstractmethod
    async def connect_usbmux(self):
        """Yield a usbmux stream scoped to the selected device."""
