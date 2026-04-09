from dataclasses import field
from typing import Callable, Optional, Sequence, Tuple, Union
from uuid import UUID, uuid4

import can
import isotp
from pydantic import ConfigDict, validate_call
from pydantic.dataclasses import dataclass

from .common import CanMessage, IsoTpAddress, IsoTpAsymmetricAddress, IsoTpMessage, IsoTpParams
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class Can(Driver):
    """
    A generic CAN bus driver.

    Available on any platform, supports many different CAN
    interfaces through the `python-can` library.
    """

    """
    The CAN channel to connect to.
    """
    channel: str | int | None

    """
    The CAN interface to use.
    """
    interface: str | None

    """
    The CAN bus instance used for communication.
    """
    bus: can.BusABC = field(init=False)

    """
    A dict of cyclic send tasks to run.
    """
    __tasks: dict[UUID, can.broadcastmanager.CyclicSendTaskABC] = field(init=False, default_factory=dict)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_can.client.CanClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self.bus = can.Bus(channel=self.channel, interface=self.interface)

    @export
    @validate_call(validate_return=True)
    def _recv_internal(self, timeout: Optional[float]) -> Tuple[Optional[CanMessage], bool]:
        msg, filtered = self.bus._recv_internal(timeout)
        if msg:
            return CanMessage.construct(msg), filtered
        return None, filtered

    @export
    @validate_call(validate_return=True)
    def send(self, msg: CanMessage, timeout: float | None = None):
        """
        Send an individual CAN message.
        """
        self.bus.send(can.Message(**msg.__dict__), timeout)

    @export
    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def _send_periodic_internal(
        self,
        msgs: Union[Sequence[CanMessage], CanMessage],
        period: float,
        duration: Optional[float] = None,
        autostart: bool = True,
        modifier_callback: Optional[Callable[[can.Message], None]] = None,
    ) -> UUID:
        assert modifier_callback is None
        task = self.bus._send_periodic_internal(msgs, period, duration, autostart, modifier_callback)
        uuid = uuid4()
        self.__tasks[uuid] = task
        return uuid

    @export
    @validate_call(validate_return=True)
    def _start_task(self, uuid: UUID) -> None:
        self.__tasks[uuid].start()

    @export
    @validate_call(validate_return=True)
    def _stop_task(self, uuid: UUID) -> None:
        self.__tasks.pop(uuid).stop()

    @export
    @validate_call(validate_return=True)
    def state(self, value: can.BusState | None = None) -> can.BusState | None:
        """
        The current state of the CAN bus.
        """
        if value:
            self.bus.state = value
            return None
        else:
            return self.bus.state

    @export
    @validate_call(validate_return=True)
    def protocol(self) -> can.CanProtocol:
        """
        Get the CAN protocol supported by the bus.
        """
        return self.bus.protocol

    @export
    @validate_call(validate_return=True)
    def channel_info(self) -> str:
        """
        Get the CAN channel info.
        """
        return self.bus.channel_info

    @export
    # python-can bug
    # https://docs.pydantic.dev/2.8/errors/usage_errors/#typed-dict-version
    # @validate_call(validate_return=True)
    def _apply_filters(self, filters: Optional[can.typechecking.CanFilters]) -> None:
        self.bus._apply_filters(filters)

    @export
    @validate_call(validate_return=True)
    def flush_tx_buffer(self) -> None:
        """
        Flush the transmission buffer.
        """
        self.bus.flush_tx_buffer()

    @export
    @validate_call(validate_return=True)
    def shutdown(self) -> None:
        """
        Shutdown the bus.
        """
        self.bus.shutdown()


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class IsoTpPython(Driver):
    """
    Pure python ISO-TP socket driver

    Available on any platform, moderate performance and reliability,
    wide support for non-standard hardware interfaces
    """

    """
    The CAN channel to connect to.
    """
    channel: str | int | None

    """
    The CAN interface to use.
    """
    interface: str | None

    """
    The ISO-TP addressing to use.
    """
    address: isotp.Address

    """
    The ISO-TP parameters.
    """
    params: IsoTpParams = field(default_factory=IsoTpParams)

    """
    The read timeout for the bus.
    """
    read_timeout: float = 0.05

    """
    The CAN bus instance used for communication.
    """
    bus: can.BusABC = field(init=False)

    """
    The CAN bus notifier instance used to receive messages.
    """
    notifier: can.Notifier = field(init=False)

    """
    The ISO-TP CAN layer configured to use `python-can`.
    """
    stack: isotp.NotifierBasedCanStack = field(init=False)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_can.client.IsoTpClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self.bus = can.Bus(channel=self.channel, interface=self.interface)
        self.notifier = can.Notifier(self.bus, [])
        self.stack = isotp.NotifierBasedCanStack(
            self.bus,
            self.notifier,
            address=self.address,
            params=self.params.model_dump(),
            read_timeout=self.read_timeout,
        )

    @export
    @validate_call(validate_return=True)
    def start(self) -> None:
        """
        Start listening for messages.
        """
        self.stack.start()

    @export
    @validate_call(validate_return=True)
    def stop(self) -> None:
        """
        Stop listening for messages.
        """
        self.stack.stop()

    @export
    @validate_call(validate_return=True)
    def send(
        self, msg: IsoTpMessage, target_address_type: int | None = None, send_timeout: float | None = None
    ) -> None:
        """
        Enqueue an ISO-TP frame to send over the CAN network.
        """
        return self.stack.send(msg.data, target_address_type, send_timeout)

    @export
    @validate_call(validate_return=True)
    def recv(self, block: bool = False, timeout: float | None = None) -> IsoTpMessage:
        """
        Dequeue an ISO-TP frame from the reception queue if available.
        """
        return IsoTpMessage.model_construct(data=self.stack.recv(block, timeout))

    @export
    @validate_call(validate_return=True)
    def available(self) -> bool:
        """
        Returns `True` if an ISO-TP frame is awaiting in the reception queue, `False` otherwise.
        """
        return self.stack.available()

    @export
    @validate_call(validate_return=True)
    def transmitting(self) -> bool:
        """
        Returns `True` if an ISO-TP frame is being transmitted, `False` otherwise.
        """
        return self.stack.transmitting()

    @export
    @validate_call(validate_return=True)
    def set_address(self, address: IsoTpAddress | IsoTpAsymmetricAddress) -> None:
        """
        Sets the layer address. Can be set after initialization if needed.
        May cause a timeout if called while a transmission is active.
        """
        self.stack.set_address(address.dump())

    @export
    @validate_call(validate_return=True)
    def stop_sending(self) -> None:
        """
        Stop sending messages.
        """
        self.stack.stop_sending()

    @export
    @validate_call(validate_return=True)
    def stop_receiving(self) -> None:
        """
        Stop receiving messages.
        """
        self.stack.stop_receiving()


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class IsoTpSocket(Driver):
    """
    Linux kernel ISO-TP socket driver

    Available since kernel 5.10, good performance and reliability,
    only supports standard hardware interfaces
    """

    """
    The CAN channel to connect to.
    """
    channel: str

    """
    The ISO-TP addressing to use.
    """
    address: isotp.Address
    params: IsoTpParams = field(default_factory=IsoTpParams)

    sock: isotp.socket | None = field(init=False, default=None)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_can.client.IsoTpClient"

    @export
    @validate_call(validate_return=True)
    def start(self) -> None:
        """
        Start listening for messages.
        """
        if self.sock:
            raise ValueError("socket already started")
        self.sock = isotp.socket()
        self.params.apply(self.sock)
        self.sock.bind(self.channel, self.address)

    @export
    @validate_call(validate_return=True)
    def stop(self) -> None:
        """
        Stop listening for messages.
        """
        if not self.sock:
            raise ValueError("socket not started")
        self.sock.close()
        self.sock = None

    @export
    @validate_call(validate_return=True)
    def send(
        self, msg: IsoTpMessage, target_address_type: int | None = None, send_timeout: float | None = None
    ) -> None:
        """
        Enqueue an ISO-TP frame to send over the CAN network.
        """
        if not self.sock:
            raise ValueError("socket not started")
        self.sock.send(msg.data)

    @export
    @validate_call(validate_return=True)
    def recv(self, block: bool = False, timeout: float | None = None) -> IsoTpMessage:
        """
        Dequeue an ISO-TP frame from the reception queue if available.
        """
        if not self.sock:
            raise ValueError("socket not started")
        return IsoTpMessage.model_construct(data=self.sock.recv())

    @export
    @validate_call(validate_return=True)
    def available(self) -> bool:
        """
        Not supported by the socket ISO-TP driver.
        """
        raise NotImplementedError

    @export
    @validate_call(validate_return=True)
    def transmitting(self) -> bool:
        """
        Not supported by the socket ISO-TP driver.
        """
        raise NotImplementedError

    @export
    @validate_call(validate_return=True)
    def set_address(self, address: IsoTpAddress | IsoTpAsymmetricAddress) -> None:
        """
        Not supported by the socket ISO-TP driver.
        """
        raise NotImplementedError

    @export
    @validate_call(validate_return=True)
    def stop_sending(self) -> None:
        """
        Not supported by the socket ISO-TP driver.
        """
        raise NotImplementedError

    @export
    @validate_call(validate_return=True)
    def stop_receiving(self) -> None:
        """
        Not supported by the socket ISO-TP driver.
        """
        raise NotImplementedError
