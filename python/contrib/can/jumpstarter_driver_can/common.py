from typing import Optional

from pydantic import Base64Bytes, BaseModel


class CanMessage(BaseModel):
    timestamp: float
    arbitration_id: int
    is_extended_id: bool
    is_remote_frame: bool
    is_error_frame: bool
    channel: Optional[int | str]
    dlc: Optional[int]
    data: Optional[Base64Bytes]
    is_fd: bool
    is_rx: bool
    bitrate_switch: bool
    error_state_indicator: bool

    @classmethod
    def construct(cls, msg):
        return cls.model_construct(
            timestamp=msg.timestamp,
            arbitration_id=msg.arbitration_id,
            is_extended_id=msg.is_extended_id,
            is_remote_frame=msg.is_remote_frame,
            is_error_frame=msg.is_error_frame,
            channel=msg.channel,
            dlc=msg.dlc,
            data=msg.data,
            is_fd=msg.is_fd,
            is_rx=msg.is_rx,
            bitrate_switch=msg.bitrate_switch,
            error_state_indicator=msg.error_state_indicator,
        )
