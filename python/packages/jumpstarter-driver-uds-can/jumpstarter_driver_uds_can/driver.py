from __future__ import annotations

from dataclasses import field

import can
import isotp
from jumpstarter_driver_can.common import IsoTpParams
from jumpstarter_driver_uds.driver import UdsInterface
from pydantic import ConfigDict
from pydantic.dataclasses import dataclass
from udsoncan.client import Client as UdsoncanClient
from udsoncan.connections import PythonIsoTpConnection

from jumpstarter.driver import Driver


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class UdsCan(UdsInterface, Driver):
    """UDS (Unified Diagnostic Services) driver over CAN/ISO-TP transport.

    Provides core UDS diagnostic operations (ISO-14229) over
    ISO-TP (ISO-15765) on CAN bus (ISO-11898) using the
    python-can, can-isotp, and udsoncan libraries.
    """

    channel: str
    interface: str = "socketcan"
    rxid: int
    txid: int
    request_timeout: float = 5.0
    isotp_params: IsoTpParams = field(default_factory=IsoTpParams)

    _bus: can.BusABC = field(init=False, repr=False)
    _notifier: can.Notifier = field(init=False, repr=False)
    _stack: isotp.NotifierBasedCanStack = field(init=False, repr=False)
    _uds_conn: PythonIsoTpConnection = field(init=False, repr=False)
    _uds_client: UdsoncanClient = field(init=False, repr=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self._bus = can.Bus(channel=self.channel, interface=self.interface)
        self._notifier = can.Notifier(self._bus, [])

        address = isotp.Address(rxid=self.rxid, txid=self.txid)
        self._stack = isotp.NotifierBasedCanStack(
            self._bus,
            self._notifier,
            address=address,
            params=self.isotp_params.model_dump() if self.isotp_params else None,
        )

        self._uds_conn = PythonIsoTpConnection(self._stack)
        self._uds_client = UdsoncanClient(self._uds_conn, request_timeout=self.request_timeout)
        self._uds_client.open()

    def close(self):
        """Close the UDS connection and CAN bus."""
        self._uds_client.close()
        self._bus.shutdown()
        super().close()
