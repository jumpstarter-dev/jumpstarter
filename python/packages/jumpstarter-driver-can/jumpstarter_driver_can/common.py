from typing import Optional

import isotp
from isotp.address import AddressingMode
from pydantic import Base64Bytes, BaseModel


class CanMessage(BaseModel):
    """
    Internal CAN message type used for gRPC transmission.
    """

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


class IsoTpParams(BaseModel):
    """
    ISO-TP configuration parameters.
    """

    stmin: int = 0
    blocksize: int = 8
    tx_data_length: int = 8
    tx_data_min_length: int | None = None
    override_receiver_stmin: float | None = None
    rx_flowcontrol_timeout: int = 1000
    rx_consecutive_frame_timeout: int = 1000
    tx_padding: int | None = None
    wftmax: int = 0
    max_frame_size: int = 4095
    can_fd: bool = False
    bitrate_switch: bool = False
    default_target_address_type: isotp.TargetAddressType = isotp.TargetAddressType.Physical
    rate_limit_enable: bool = False
    rate_limit_max_bitrate: int = 10000000
    rate_limit_window_size: float = 0.2
    listen_mode: bool = False
    blocking_send: bool = False

    def apply(self, socket):
        socket.set_opts(
            optflag=None,
            frame_txtime=None,
            ext_address=None,
            txpad=self.tx_padding,
            rxpad=None,
            rx_ext_address=None,
            tx_stmin=None,
        )
        socket.set_fc_opts(bs=self.blocksize, stmin=self.stmin, wftmax=self.wftmax)
        socket.set_ll_opts(
            mtu=isotp.socket.LinkLayerProtocol.CAN_FD if self.can_fd else isotp.socket.LinkLayerProtocol.CAN,
            tx_dl=self.tx_data_length,
            tx_flags=None,
        )


class IsoTpMessage(BaseModel):
    """
    An ISO-TP CAN message.
    """

    data: Optional[Base64Bytes]


class IsoTpAddress(BaseModel):
    """
    An ISO-TP address set.
    """

    addressing_mode: AddressingMode
    txid: int | None
    rxid: int | None
    target_address: int | None
    source_address: int | None
    physical_id: int | None
    functional_id: int | None
    address_extension: int | None
    rx_only: bool
    tx_only: bool

    @classmethod
    def validate(cls, addr: isotp.Address):
        return cls(
            addressing_mode=addr._addressing_mode,
            txid=addr._txid,
            rxid=addr._rxid,
            target_address=addr._target_address,
            source_address=addr._source_address,
            physical_id=addr.physical_id  # ty: ignore[possibly-unbound-attribute]
            if hasattr(addr, "physical_id")
            else None,
            functional_id=addr.functional_id  # ty: ignore[possibly-unbound-attribute]
            if hasattr(addr, "functional_id")
            else None,
            address_extension=addr._address_extension,
            rx_only=addr._rx_only,
            tx_only=addr._tx_only,
        )

    def dump(self):
        return isotp.Address(
            addressing_mode=self.addressing_mode,
            txid=self.txid,
            rxid=self.rxid,
            target_address=self.target_address,
            source_address=self.source_address,
            physical_id=self.physical_id,
            functional_id=self.functional_id,
            address_extension=self.address_extension,
            rx_only=self.rx_only,
            tx_only=self.tx_only,
        )


class IsoTpAsymmetricAddress(BaseModel):
    """
    An asymmetric ISO-TP address.
    """

    tx_addr: IsoTpAddress
    rx_addr: IsoTpAddress

    @classmethod
    def validate(cls, addr: isotp.AsymmetricAddress):
        return cls(
            tx_addr=IsoTpAddress.validate(addr.tx_addr),
            rx_addr=IsoTpAddress.validate(addr.rx_addr),
        )

    def dump(self):
        return isotp.AsymmetricAddress(
            tx_addr=self.tx_addr.dump(),
            rx_addr=self.rx_addr.dump(),
        )
