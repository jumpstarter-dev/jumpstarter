from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Callable, List, Optional, Sequence, Tuple
from uuid import UUID

import can
import isotp
from can.bus import _SelfRemovingCyclicTask
from pydantic import ConfigDict, validate_call

from .common import CanMessage, IsoTpAddress, IsoTpAsymmetricAddress, IsoTpMessage
from jumpstarter.client import DriverClient


@dataclass(kw_only=True)
class RemoteCyclicSendTask(can.broadcastmanager.CyclicSendTaskABC):
    client: CanClient
    uuid: UUID

    def start(self) -> None:
        self.client.call("_start_task", self.uuid)

    def stop(self) -> None:
        self.client.call("_stop_task", self.uuid)


@dataclass(kw_only=True)
class CanClient(DriverClient, can.BusABC):
    """
    A generic CAN client for sending/recieving traffic to/from an exported CAN bus.
    """

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self._periodic_tasks: List[_SelfRemovingCyclicTask] = []
        self._filters = None
        self._is_shutdown: bool = False

    @property
    @validate_call(validate_return=True)
    def state(self) -> can.BusState:
        """
        The current state of the CAN bus.
        """
        return self.call("state")

    @state.setter
    @validate_call(validate_return=True)
    def state(self, value: can.BusState) -> None:
        """
        Set the state of the CAN bus.
        """
        self.call("state", value)

    @cached_property
    @validate_call(validate_return=True)
    def channel_info(self) -> str:
        """
        Get the CAN channel info.
        """
        return self.call("channel_info")

    @cached_property
    @validate_call(validate_return=True)
    def protocol(self) -> can.CanProtocol:
        """
        Get the CAN protocol supported by the bus.
        """
        return self.call("protocol")

    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def _recv_internal(self, timeout: Optional[float]) -> Tuple[Optional[can.Message], bool]:
        msg, filtered = self.call("_recv_internal", timeout)
        if msg:
            return can.Message(**CanMessage.model_validate(msg).__dict__), filtered
        return None, filtered

    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def send(self, msg: can.Message, timeout: Optional[float] = None) -> None:
        """
        Send an individual CAN message.
        """
        self.call("send", CanMessage.construct(msg), timeout)

    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def _send_periodic_internal(
        self,
        msgs: Sequence[can.Message],
        period: float,
        duration: Optional[float] = None,
        autostart: bool = True,
        modifier_callback: Optional[Callable[[can.Message], None]] = None,
    ) -> can.broadcastmanager.CyclicSendTaskABC:
        if modifier_callback:
            return super()._send_periodic_internal(msgs, period, duration, autostart, modifier_callback)
        else:
            msgs = [CanMessage.construct(msg) for msg in msgs]
            return RemoteCyclicSendTask(
                client=self, uuid=self.call("_send_periodic_internal", msgs, period, duration, autostart)
            )

    # python-can bug
    # https://docs.pydantic.dev/2.8/errors/usage_errors/#typed-dict-version
    # @validate_call(validate_return=True)
    def _apply_filters(self, filters: Optional[can.typechecking.CanFilters]) -> None:
        self.call("_apply_filters", filters)

    @validate_call(validate_return=True)
    def flush_tx_buffer(self) -> None:
        """
        Flush the transmission buffer.
        """
        self.call("flush_tx_buffer")

    @validate_call(validate_return=True)
    def shutdown(self) -> None:
        """
        Shutdown the bus.
        """
        self.call("shutdown")
        super().shutdown()


@dataclass(kw_only=True)
class IsoTpClient(DriverClient):
    """
    An ISO-TP CAN client for sending/recieving ISO-TP frames to/from an exported CAN bus.
    """

    def start(self) -> None:
        """
        Start listening for messages.
        """
        self.call("start")

    def stop(self) -> None:
        """
        Stop listening for messages.
        """
        self.call("stop")

    def send(self, data: bytes, target_address_type: int | None = None, send_timeout: float | None = None) -> None:
        """
        Enqueue an ISO-TP frame to send over the CAN network.
        """
        return self.call("send", IsoTpMessage.model_construct(data=data), target_address_type, send_timeout)

    def recv(self, block: bool = False, timeout: float | None = None) -> bytes | None:
        """
        Dequeue an ISO-TP frame from the reception queue if available.
        """
        return IsoTpMessage.model_validate(self.call("recv", block, timeout)).data

    def available(self) -> bool:
        """
        Returns `True` if an ISO-TP frame is awaiting in the reception queue, `False` otherwise.
        """
        return self.call("available")

    def transmitting(self) -> bool:
        """
        Returns `True` if an ISO-TP frame is being transmitted, `False` otherwise.
        """
        return self.call("transmitting")

    def set_address(self, address: isotp.Address | isotp.AsymmetricAddress) -> None:
        """
        Sets the layer address. Can be set after initialization if needed.
        May cause a timeout if called while a transmission is active.
        """
        match address:
            case isotp.Address():
                return self.call("set_address", IsoTpAddress.validate(address))
            case isotp.AsymmetricAddress():
                return self.call("set_address", IsoTpAsymmetricAddress.validate(address))
            case _:
                raise ValueError("address not isotp.Address | isotp.AsymmetricAddress")

    def stop_sending(self) -> None:
        """
        Stop sending messages.
        """
        self.call("stop_sending")

    def stop_receiving(self) -> None:
        """
        Stop receiving messages.
        """
        self.call("stop_receiving")
