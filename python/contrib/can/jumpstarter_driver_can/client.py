from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Callable, List, Optional, Sequence, Tuple
from uuid import UUID

import can
from can.bus import _SelfRemovingCyclicTask
from pydantic import ConfigDict, validate_call

from jumpstarter.client import DriverClient

from .common import CanMessage


@dataclass(kw_only=True)
class RemoteCyclicSendTask(can.broadcastmanager.CyclicSendTaskABC):
    client: CanClient
    uuid: UUID

    def stop(self) -> None:
        self.client.call("_stop_task", self.uuid)


@dataclass(kw_only=True)
class CanClient(DriverClient, can.BusABC):
    def __post_init__(self):
        self._periodic_tasks: List[_SelfRemovingCyclicTask] = []
        self._filters = None
        self._is_shutdown: bool = False

        super().__post_init__()

    @property
    @validate_call(validate_return=True)
    def state(self) -> can.BusState:
        return self.call("state")

    @state.setter
    @validate_call(validate_return=True)
    def state(self, value: can.BusState) -> None:
        self.call("state", value)

    @cached_property
    @validate_call(validate_return=True)
    def channel_info(self) -> str:
        return self.call("channel_info")

    @cached_property
    @validate_call(validate_return=True)
    def protocol(self) -> can.CanProtocol:
        return self.call("protocol")

    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def _recv_internal(self, timeout: Optional[float]) -> Tuple[Optional[can.Message], bool]:
        msg, filtered = self.call("_recv_internal", timeout)
        if msg:
            return can.Message(**CanMessage.model_validate(msg).__dict__), filtered
        return None, filtered

    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def send(self, msg: can.Message, timeout: Optional[float] = None) -> None:
        self.call("send", CanMessage.construct(msg), timeout)

    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def _send_periodic_internal(
        self,
        msgs: Sequence[can.Message],
        period: float,
        duration: Optional[float] = None,
        modifier_callback: Optional[Callable[[can.Message], None]] = None,
    ) -> can.broadcastmanager.CyclicSendTaskABC:
        if modifier_callback:
            return super()._send_periodic_internal(msgs, period, duration, modifier_callback)
        else:
            msgs = [CanMessage.construct(msg) for msg in msgs]
            return RemoteCyclicSendTask(client=self, uuid=self.call("_send_periodic_internal", msgs, period, duration))

    # python-can bug
    # https://docs.pydantic.dev/2.8/errors/usage_errors/#typed-dict-version
    # @validate_call(validate_return=True)
    def _apply_filters(self, filters: Optional[can.typechecking.CanFilters]) -> None:
        self.call("_apply_filters", filters)

    @validate_call(validate_return=True)
    def flush_tx_buffer(self) -> None:
        self.call("flush_tx_buffer")

    @validate_call(validate_return=True)
    def shutdown(self) -> None:
        self.call("shutdown")
        super().shutdown()
