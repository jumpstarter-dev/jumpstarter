from __future__ import annotations

from dataclasses import field

from doipclient import DoIPClient
from doipclient.connectors import DoIPClientUDSConnector
from jumpstarter_driver_uds.driver import UdsInterface
from pydantic import ConfigDict
from pydantic.dataclasses import dataclass
from udsoncan.client import Client as UdsoncanClient

from jumpstarter.driver import Driver


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class UdsDoip(UdsInterface, Driver):
    """UDS (Unified Diagnostic Services) driver over DoIP transport.

    Provides core UDS diagnostic operations (ISO-14229) over
    DoIP (ISO-13400) using the doipclient and udsoncan libraries.
    """

    ecu_ip: str
    ecu_logical_address: int
    tcp_port: int = 13400
    protocol_version: int = 2
    client_logical_address: int = 0x0E00
    auto_reconnect_tcp: bool = False
    request_timeout: float = 5.0

    _doip_client: DoIPClient = field(init=False, repr=False)
    _uds_client: UdsoncanClient = field(init=False, repr=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self._doip_client = DoIPClient(
            self.ecu_ip,
            self.ecu_logical_address,
            tcp_port=self.tcp_port,
            protocol_version=self.protocol_version,
            client_logical_address=self.client_logical_address,
            auto_reconnect_tcp=self.auto_reconnect_tcp,
        )
        conn = DoIPClientUDSConnector(self._doip_client)
        self._uds_client = UdsoncanClient(conn, request_timeout=self.request_timeout)
        self._uds_client.open()

    def close(self):
        """Close the UDS and DoIP connections."""
        try:
            self._uds_client.close()
        except Exception:
            pass
        try:
            self._doip_client.close()
        except Exception:
            pass
        super().close()
