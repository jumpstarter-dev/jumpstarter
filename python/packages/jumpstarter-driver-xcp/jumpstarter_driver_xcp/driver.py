from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import field

from pydantic import ConfigDict, validate_call
from pydantic.dataclasses import dataclass

from .common import (
    XcpChecksum,
    XcpConnectionInfo,
    XcpDaqInfo,
    XcpEthProtocol,
    XcpIdentifier,
    XcpProgramInfo,
    XcpStatusResponse,
    XcpTransport,
)
from jumpstarter.driver import Driver, export


def _build_config_content(
    transport: XcpTransport,
    host: str | None,
    port: int,
    protocol: XcpEthProtocol,
    can_interface: str | None,
    channel: str | int | None,
    bitrate: int | None,
    can_id_master: int | None,
    can_id_slave: int | None,
) -> str:
    lines = ["c = get_config()"]
    lines.append(f'c.Transport.layer = "{transport.value}"')

    if transport == XcpTransport.ETH:
        if host:
            lines.append(f'c.Transport.Eth.host = "{host}"')
        lines.append(f"c.Transport.Eth.port = {port}")
        lines.append(f'c.Transport.Eth.protocol = "{protocol.value}"')
    elif transport == XcpTransport.CAN:
        if can_interface:
            lines.append(f'c.Transport.Can.interface = "{can_interface}"')
        if channel is not None:
            lines.append(f"c.Transport.Can.channel = {channel}")
        if bitrate is not None:
            lines.append(f"c.Transport.Can.bitrate = {bitrate}")
        if can_id_master is not None:
            lines.append(f"c.Transport.Can.can_id_master = {can_id_master}")
        if can_id_slave is not None:
            lines.append(f"c.Transport.Can.can_id_slave = {can_id_slave}")

    return "\n".join(lines) + "\n"


def _create_xcp_master(
    transport: XcpTransport,
    config_file: str | None,
    host: str | None,
    port: int,
    protocol: XcpEthProtocol,
    can_interface: str | None,
    channel: str | int | None,
    bitrate: int | None,
    can_id_master: int | None,
    can_id_slave: int | None,
):
    """Create a pyxcp Master instance from driver parameters.

    Uses pyxcp's ArgumentParser with a generated or user-provided config file.
    sys.argv is temporarily overridden to pass transport arguments.
    """
    from pyxcp.cmdline import ArgumentParser as XcpArgumentParser

    tmp_path = None
    try:
        if config_file is None:
            content = _build_config_content(
                transport, host, port, protocol,
                can_interface, channel, bitrate, can_id_master, can_id_slave,
            )
            fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="xcp_cfg_")
            with os.fdopen(fd, "w") as f:
                f.write(content)
            cfg_path = tmp_path
        else:
            cfg_path = config_file

        saved_argv = sys.argv
        sys.argv = [
            "jumpstarter-xcp",
            "-t", transport.value.lower(),
            "--config", cfg_path,
        ]
        try:
            ap = XcpArgumentParser(description="Jumpstarter XCP Driver")
            master = ap.run()
        finally:
            sys.argv = saved_argv
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return master


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class Xcp(Driver):
    """XCP (Universal Measurement and Calibration Protocol) driver.

    Wraps the pyXCP library to provide remote access to XCP-enabled
    devices (ECUs) for measurement, calibration, DAQ, and programming.
    """

    transport: XcpTransport = XcpTransport.ETH
    host: str | None = "localhost"
    port: int = 5555
    protocol: XcpEthProtocol = XcpEthProtocol.TCP
    can_interface: str | None = None
    channel: str | int | None = None
    bitrate: int | None = None
    can_id_master: int | None = None
    can_id_slave: int | None = None
    config_file: str | None = None

    _master: object = field(init=False, repr=False, default=None)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_xcp.client.XcpClient"

    def _ensure_master(self):
        if self._master is None:
            self._master = _create_xcp_master(
                transport=self.transport,
                config_file=self.config_file,
                host=self.host,
                port=self.port,
                protocol=self.protocol,
                can_interface=self.can_interface,
                channel=self.channel,
                bitrate=self.bitrate,
                can_id_master=self.can_id_master,
                can_id_slave=self.can_id_slave,
            )

    # --- Session Management ---

    @export
    @validate_call(validate_return=True)
    def connect(self, mode: int = 0) -> XcpConnectionInfo:
        """Connect to the XCP slave and return negotiated properties."""
        self._ensure_master()
        self._master.connect(mode)
        sp = self._master.slaveProperties
        return XcpConnectionInfo(
            max_cto=sp.get("maxCto", 0),
            max_dto=sp.get("maxDto", 0),
            byte_order=str(sp.get("byteOrder", "")),
            supports_pgm=sp.get("supportsPgm", False),
            supports_stim=sp.get("supportsStim", False),
            supports_daq=sp.get("supportsDaq", False),
            supports_calpag=sp.get("supportsCalpag", False),
            protocol_layer_version=sp.get("protocolLayerVersion", 0),
            transport_layer_version=sp.get("transportLayerVersion", 0),
            address_granularity=str(sp.get("addressGranularity", "")),
            slave_block_mode=sp.get("slaveBlockMode", False),
        )

    @export
    @validate_call(validate_return=True)
    def disconnect(self) -> None:
        """Disconnect from the XCP slave."""
        if self._master is not None:
            self._master.close()
            self._master = None

    @export
    @validate_call(validate_return=True)
    def get_id(self, id_type: int = 1) -> XcpIdentifier:
        """Get the slave identifier string."""
        self._ensure_master()
        result = self._master.identifier(id_type)
        return XcpIdentifier(id_type=id_type, identifier=str(result))

    @export
    @validate_call(validate_return=True)
    def get_status(self) -> XcpStatusResponse:
        """Get the current session status and resource protection."""
        self._ensure_master()
        status = self._master.getStatus()
        protection = self._master.getCurrentProtectionStatus()
        return XcpStatusResponse(
            session_status={
                k: v for k, v in status.items()
                if isinstance(v, (bool, int, str))
            } if hasattr(status, "items") else {},
            resource_protection=protection if isinstance(protection, dict) else {},
        )

    # --- Security ---

    @export
    @validate_call(validate_return=True)
    def unlock(self, resources: list[str] | None = None) -> dict[str, bool]:
        """Perform conditional unlock (seed & key) for protected resources.

        Uses the seed-n-key function/DLL configured in the pyxcp config.
        Returns the current protection status after the unlock attempt.
        """
        self._ensure_master()
        self._master.cond_unlock(resources)
        return self._master.getCurrentProtectionStatus()

    # --- Memory Access (Measurement / Calibration) ---

    @export
    @validate_call(validate_return=True)
    def upload(self, length: int, address: int, ext: int = 0) -> bytes:
        """Read memory from the XCP slave (short upload)."""
        self._ensure_master()
        data = self._master.shortUpload(length, address, ext)
        return bytes(data)

    @export
    @validate_call(validate_return=True)
    def download(self, address: int, data: bytes, ext: int = 0) -> None:
        """Write data to the XCP slave memory."""
        self._ensure_master()
        self._master.setMta(address, ext)
        self._master.download(data)

    @export
    @validate_call(validate_return=True)
    def set_mta(self, address: int, ext: int = 0) -> None:
        """Set the Memory Transfer Address pointer."""
        self._ensure_master()
        self._master.setMta(address, ext)

    @export
    @validate_call(validate_return=True)
    def build_checksum(self, block_size: int) -> XcpChecksum:
        """Compute a checksum over a memory block (starting at current MTA)."""
        self._ensure_master()
        result = self._master.buildChecksum(block_size)
        return XcpChecksum(
            checksum_type=result.checksumType if hasattr(result, "checksumType") else 0,
            checksum_value=result.checksum if hasattr(result, "checksum") else 0,
        )

    # --- DAQ (Data Acquisition) ---

    @export
    @validate_call(validate_return=True)
    def get_daq_info(self) -> XcpDaqInfo:
        """Get DAQ processor, resolution, and event channel information."""
        self._ensure_master()
        info = self._master.getDaqInfo()
        return XcpDaqInfo(
            processor=info.get("processor", {}),
            resolution=info.get("resolution", {}),
            channels=info.get("channels", []),
        )

    @export
    @validate_call(validate_return=True)
    def free_daq(self) -> None:
        """Free all DAQ lists."""
        self._ensure_master()
        self._master.freeDaq()

    @export
    @validate_call(validate_return=True)
    def alloc_daq(self, daq_count: int) -> None:
        """Allocate DAQ lists."""
        self._ensure_master()
        self._master.allocDaq(daq_count)

    @export
    @validate_call(validate_return=True)
    def alloc_odt(self, daq_list_number: int, odt_count: int) -> None:
        """Allocate ODTs for a DAQ list."""
        self._ensure_master()
        self._master.allocOdt(daq_list_number, odt_count)

    @export
    @validate_call(validate_return=True)
    def alloc_odt_entry(
        self, daq_list_number: int, odt_number: int, odt_entries_count: int
    ) -> None:
        """Allocate ODT entries for a specific ODT."""
        self._ensure_master()
        self._master.allocOdtEntry(daq_list_number, odt_number, odt_entries_count)

    @export
    @validate_call(validate_return=True)
    def set_daq_ptr(self, daq_list: int, odt: int, entry: int) -> None:
        """Set the DAQ list pointer."""
        self._ensure_master()
        self._master.setDaqPtr(daq_list, odt, entry)

    @export
    @validate_call(validate_return=True)
    def write_daq(
        self, bit_offset: int, size: int, ext: int, address: int
    ) -> None:
        """Write a DAQ entry (configure what to measure)."""
        self._ensure_master()
        self._master.writeDaq(bit_offset, size, ext, address)

    @export
    @validate_call(validate_return=True)
    def set_daq_list_mode(
        self, mode: int, daq_list: int, event: int, prescaler: int, priority: int
    ) -> None:
        """Set the mode for a DAQ list."""
        self._ensure_master()
        self._master.setDaqListMode(mode, daq_list, event, prescaler, priority)

    @export
    @validate_call(validate_return=True)
    def start_stop_daq_list(self, mode: int, daq_list: int) -> None:
        """Start or stop a single DAQ list."""
        self._ensure_master()
        self._master.startStopDaqList(mode, daq_list)

    @export
    @validate_call(validate_return=True)
    def start_stop_synch(self, mode: int) -> None:
        """Start or stop all DAQ lists synchronously."""
        self._ensure_master()
        self._master.startStopSynch(mode)

    # --- Programming (Flashing) ---

    @export
    @validate_call(validate_return=True)
    def program_start(self) -> XcpProgramInfo:
        """Begin a programming sequence."""
        self._ensure_master()
        result = self._master.programStart()
        return XcpProgramInfo(
            comm_mode_pgm=getattr(result, "commModePgm", 0),
            max_cto_pgm=getattr(result, "maxCtoPgm", 0),
            max_bs_pgm=getattr(result, "maxBsPgm", 0),
            min_st_pgm=getattr(result, "minStPgm", 0),
            queue_size_pgm=getattr(result, "queueSizePgm", 0),
        )

    @export
    @validate_call(validate_return=True)
    def program_clear(self, clear_range: int, mode: int = 0) -> None:
        """Clear (erase) a memory range for programming."""
        self._ensure_master()
        self._master.programClear(mode, clear_range)

    @export
    @validate_call(validate_return=True)
    def program(self, data: bytes, block_length: int = 0) -> None:
        """Download program data to the slave."""
        self._ensure_master()
        self._master.program(data, block_length or len(data))

    @export
    @validate_call(validate_return=True)
    def program_reset(self) -> None:
        """Reset the slave after programming."""
        self._ensure_master()
        self._master.programReset()
