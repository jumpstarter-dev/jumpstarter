import asyncio
import socket
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Dict

from pysnmp.carrier.asyncio.dgram import udp
from pysnmp.entity import config, engine
from pysnmp.entity.rfc3413 import cmdgen
from pysnmp.proto import rfc1902

from jumpstarter.driver import Driver, export


class AuthProtocol(str, Enum):
    NONE = "NONE"
    MD5 = "MD5"
    SHA = "SHA"


class PrivProtocol(str, Enum):
    NONE = "NONE"
    DES = "DES"
    AES = "AES"


class PowerState(IntEnum):
    OFF = 0
    ON = 1


class SNMPError(Exception):
    """Base exception for SNMP errors"""

    pass


@dataclass(kw_only=True)
class SNMPServer(Driver):
    """SNMP Power Control Driver"""

    host: str = field()
    user: str = field()
    port: int = field(default=161)
    plug: int = field()
    oid: str = field(default="1.3.6.1.4.1.13742.6.4.1.2.1.2.1")
    auth_protocol: AuthProtocol = field(default=AuthProtocol.NONE)
    auth_key: str | None = field(default=None)
    priv_protocol: PrivProtocol = field(default=PrivProtocol.NONE)
    priv_key: str | None = field(default=None)
    timeout: float = field(default=5.0)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        try:
            self.ip_address = socket.gethostbyname(self.host)
            self.logger.debug(f"Resolved {self.host} to {self.ip_address}")
        except socket.gaierror as e:
            raise SNMPError(f"Failed to resolve hostname {self.host}: {e}") from e

        self.full_oid = tuple(int(x) for x in self.oid.split(".")) + (self.plug,)

    def _setup_snmp(self):
        """
        Set up SNMP engine with v3 authentication.

        Note: This method should be called from an async context in Python 3.14+
        to ensure an event loop is available for UdpAsyncioTransport.
        """
        snmp_engine = engine.SnmpEngine()

        AUTH_PROTOCOLS = {
            AuthProtocol.NONE: config.USM_AUTH_NONE,
            AuthProtocol.MD5: config.USM_AUTH_HMAC96_MD5,
            AuthProtocol.SHA: config.USM_AUTH_HMAC96_SHA,
        }

        PRIV_PROTOCOLS = {
            PrivProtocol.NONE: config.USM_PRIV_NONE,
            PrivProtocol.DES: config.USM_PRIV_CBC56_DES,
            PrivProtocol.AES: config.USM_PRIV_CFB128_AES,
        }

        auth_protocol = AUTH_PROTOCOLS[self.auth_protocol]
        priv_protocol = PRIV_PROTOCOLS[self.priv_protocol]

        if self.auth_protocol == AuthProtocol.NONE:
            security_level = "noAuthNoPriv"
        elif self.priv_protocol == PrivProtocol.NONE:
            security_level = "authNoPriv"
        else:
            security_level = "authPriv"

        if security_level == "noAuthNoPriv":
            config.add_v3_user(snmp_engine, self.user)
        elif security_level == "authNoPriv":
            if not self.auth_key:
                raise SNMPError("Authentication key required when auth_protocol is specified")
            config.add_v3_user(snmp_engine, self.user, auth_protocol, self.auth_key)
        else:
            if not self.auth_key or not self.priv_key:
                raise SNMPError("Both auth_key and priv_key required for authenticated privacy")
            config.add_v3_user(snmp_engine, self.user, auth_protocol, self.auth_key, priv_protocol, self.priv_key)

        config.add_target_parameters(snmp_engine, "my-creds", self.user, security_level)

        config.add_target_address(
            snmp_engine,
            "my-target",
            udp.DOMAIN_NAME,
            (self.ip_address, self.port),
            "my-creds",
            timeout=int(self.timeout * 100),
        )

        # Python 3.14+: UdpAsyncioTransport will use the running event loop
        # This must be called from an async context
        config.add_transport(snmp_engine, udp.DOMAIN_NAME, udp.UdpAsyncioTransport().open_client_mode())

        return snmp_engine

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_snmp.client.SNMPServerClient"

    def _create_snmp_callback(self, result: Dict[str, Any], response_received: asyncio.Event):
        def callback(snmpEngine, sendRequestHandle, errorIndication, errorStatus, errorIndex, varBinds, cbCtx):
            self.logger.debug(f"Callback {errorIndication} {errorStatus} {errorIndex} {varBinds}")
            if errorIndication:
                self.logger.error(f"SNMP error: {errorIndication}")
                result["error"] = f"SNMP error: {errorIndication}"
            elif errorStatus:
                self.logger.error(f"SNMP status: {errorStatus}")
                result["error"] = (
                    f"SNMP error: {errorStatus.prettyPrint()} at "
                    f"{varBinds[int(errorIndex) - 1][0] if errorIndex else '?'}"
                )
            else:
                result["success"] = True
                for oid, val in varBinds:
                    self.logger.debug(f"{oid.prettyPrint()} = {val.prettyPrint()}")
            self.logger.debug(f"SNMP set result: {result}")
            response_received.set()

        return callback

    async def _run_snmp_dispatcher(self, snmp_engine: engine.SnmpEngine, response_received: asyncio.Event):
        """Run the SNMP dispatcher until response is received"""
        snmp_engine.open_dispatcher()
        try:
            await response_received.wait()
        finally:
            snmp_engine.close_dispatcher()

    async def _snmp_set(self, state: PowerState):
        """
        Send an SNMP SET command to change power state.

        This method is now async to properly work with Python 3.14+
        which requires explicit event loop management.
        """
        result = {"success": False, "error": None}

        try:
            self.logger.info(f"Sending power {state.name} command to {self.host}")

            # Create event in async context (required for Python 3.14+)
            response_received = asyncio.Event()

            # Set up SNMP engine
            snmp_engine = self._setup_snmp()
            callback = self._create_snmp_callback(result, response_received)

            # Send the SNMP command
            cmdgen.SetCommandGenerator().send_varbinds(
                snmp_engine,
                "my-target",
                None,
                "",
                [(self.full_oid, rfc1902.Integer(state.value))],
                callback,
            )

            # Run dispatcher with timeout
            try:
                await asyncio.wait_for(
                    self._run_snmp_dispatcher(snmp_engine, response_received),
                    self.timeout
                )
            except asyncio.TimeoutError:
                self.logger.warning(f"SNMP operation timed out after {self.timeout} seconds")
                result["error"] = "SNMP operation timed out"

            if not result["success"]:
                raise SNMPError(result["error"] or "Unknown SNMP error")

            return f"Power {state.name} command sent successfully"

        except Exception as e:
            error_msg = f"SNMP set failed: {str(e)}"
            self.logger.error(error_msg)
            raise SNMPError(error_msg) from e

    @export
    async def on(self):
        """Turn power on (async method for Python 3.14+ compatibility)"""
        return await self._snmp_set(PowerState.ON)

    @export
    async def off(self):
        """Turn power off (async method for Python 3.14+ compatibility)"""
        return await self._snmp_set(PowerState.OFF)

    def close(self):
        """No cleanup needed since engines are created per operation"""
        if hasattr(super(), "close"):
            super().close()
