from dataclasses import field
from typing import Callable, Optional, Sequence, Tuple, Union
from uuid import UUID, uuid4

import can
from pydantic import ConfigDict, validate_call
from pydantic.dataclasses import dataclass

from jumpstarter.driver import Driver, export

from .common import CanMessage


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class Can(Driver):
    channel: str | int | None
    interface: str | None
    bus: can.Bus = field(init=False)

    __tasks: dict[UUID, can.broadcastmanager.CyclicSendTaskABC] = field(init=False, default_factory=dict)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_can.client.CanClient"

    def __post_init__(self):
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
        self.bus.send(can.Message(**msg.__dict__), timeout)

    @export
    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def _send_periodic_internal(
        self,
        msgs: Union[Sequence[CanMessage], CanMessage],
        period: float,
        duration: Optional[float] = None,
        modifier_callback: Optional[Callable[[can.Message], None]] = None,
    ) -> UUID:
        assert modifier_callback is None
        task = self.bus._send_periodic_internal(msgs, period, duration, modifier_callback)
        uuid = uuid4()
        self.__tasks[uuid] = task
        return uuid

    @export
    @validate_call(validate_return=True)
    def _stop_task(self, uuid: UUID):
        self.__tasks.pop(uuid).stop()

    @export
    @validate_call(validate_return=True)
    def state(self, value: can.BusState | None = None) -> can.BusState | None:
        if value:
            self.bus.state = value
        else:
            return self.bus.state

    @export
    @validate_call(validate_return=True)
    def protocol(self) -> can.CanProtocol:
        return self.bus.protocol

    @export
    @validate_call(validate_return=True)
    def channel_info(self) -> str:
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
        self.bus.flush_tx_buffer()

    @export
    @validate_call(validate_return=True)
    def shutdown(self) -> None:
        self.bus.shutdown()
