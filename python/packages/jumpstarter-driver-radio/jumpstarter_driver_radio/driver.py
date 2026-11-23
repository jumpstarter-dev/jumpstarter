import socket
import threading
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field

from jumpstarter_driver_network.driver import TcpNetwork

from .common import RadioPort
from jumpstarter.driver import Driver, export


class RadioInterface(metaclass=ABCMeta):
    """One of a device's radios (Wi-Fi, Bluetooth, UWB), simulated or real, as ports that can be joined.

    A radio driver sets up its own end and says which ports it offers. It does not join them to anything:
    a lease's forwards do that (JEP-0015), or a test, until those exist. Whatever has a deadline that a network
    cannot keep (an acknowledgement a simulator waits for, a radio's timing) is the driver's to handle locally.
    """

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_radio.client.RadioClient"

    @abstractmethod
    def start(self) -> None:
        """Bring the local end up. Starting it twice does nothing."""

    @abstractmethod
    def stop(self) -> None:
        """Take the local end down: its ports close, and the radio is as it was without it."""

    @abstractmethod
    def status(self) -> dict:
        """At least ``running``, ``attached`` (the local end has the radio) and ``peer`` (a far end is joined)."""

    @abstractmethod
    def ports(self) -> list[RadioPort]:
        """The ports this end offers."""


@dataclass(kw_only=True)
class MockRadio(RadioInterface, Driver):
    """A radio with nothing on the air: what goes into its port comes back out. For trying a join without a device."""

    host: str = "127.0.0.1"
    port: int = 0
    protocol: str = "echo"

    _listener: socket.socket | None = field(init=False, default=None, repr=False)
    _peers: int = field(init=False, default=0, repr=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        # (bound here so that the child knows the port; nothing is accepted until start)
        self._listener = socket.create_server((self.host, self.port))
        self.port = self._listener.getsockname()[1]
        self.children["frames"] = TcpNetwork(host=self.host, port=self.port)
        self._running = False

    def _echo(self, peer: socket.socket) -> None:
        self._peers += 1
        try:
            while data := peer.recv(65536):
                peer.sendall(data)
        except OSError:
            pass
        finally:
            self._peers -= 1
            peer.close()

    def _accept(self) -> None:
        assert self._listener is not None
        while True:
            try:
                peer, _ = self._listener.accept()
            except OSError:
                return
            if not self._running:
                peer.close()
                continue
            threading.Thread(target=self._echo, args=(peer,), daemon=True).start()

    @export
    def start(self) -> None:
        if not self._running:
            self._running = True
            threading.Thread(target=self._accept, daemon=True).start()

    @export
    def stop(self) -> None:
        self._running = False

    @export
    def status(self) -> dict:
        return {"running": self._running, "attached": self._running, "peer": self._peers > 0}

    @export
    def ports(self) -> list[RadioPort]:
        return [RadioPort(name="echo", direction="connects", protocol=self.protocol, child="frames")]

    def close(self):
        self._running = False
        if self._listener is not None:
            self._listener.close()
        super().close()
