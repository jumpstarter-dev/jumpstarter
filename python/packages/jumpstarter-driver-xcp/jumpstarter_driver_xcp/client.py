from __future__ import annotations

from dataclasses import dataclass

from .common import (
    XcpChecksum,
    XcpConnectionInfo,
    XcpDaqInfo,
    XcpIdentifier,
    XcpProgramInfo,
    XcpStatusResponse,
)
from jumpstarter.client import DriverClient


@dataclass(kw_only=True)
class XcpClient(DriverClient):
    """Client interface for XCP (Universal Measurement and Calibration Protocol).

    Provides methods for measurement, calibration, DAQ, and programming
    of XCP-enabled devices (ECUs) via the Jumpstarter remoting layer.
    """

    # --- Session Management ---

    def connect(self, mode: int = 0) -> XcpConnectionInfo:
        """Connect to the XCP slave and return negotiated properties."""
        return XcpConnectionInfo.model_validate(self.call("connect", mode))

    def disconnect(self) -> None:
        """Disconnect from the XCP slave."""
        self.call("disconnect")

    def get_id(self, id_type: int = 1) -> XcpIdentifier:
        """Get the slave identifier string."""
        return XcpIdentifier.model_validate(self.call("get_id", id_type))

    def get_status(self) -> XcpStatusResponse:
        """Get the current session status and resource protection."""
        return XcpStatusResponse.model_validate(self.call("get_status"))

    # --- Security ---

    def unlock(self, resources: list[str] | None = None) -> dict[str, bool]:
        """Perform conditional unlock (seed & key) for protected resources."""
        return self.call("unlock", resources)

    # --- Memory Access (Measurement / Calibration) ---

    def upload(self, length: int, address: int, ext: int = 0) -> bytes:
        """Read memory from the XCP slave."""
        return self.call("upload", length, address, ext)

    def download(self, address: int, data: bytes, ext: int = 0) -> None:
        """Write data to the XCP slave memory."""
        self.call("download", address, data, ext)

    def set_mta(self, address: int, ext: int = 0) -> None:
        """Set the Memory Transfer Address pointer."""
        self.call("set_mta", address, ext)

    def build_checksum(self, block_size: int) -> XcpChecksum:
        """Compute a checksum over a memory block (starting at current MTA)."""
        return XcpChecksum.model_validate(self.call("build_checksum", block_size))

    # --- DAQ (Data Acquisition) ---

    def get_daq_info(self) -> XcpDaqInfo:
        """Get DAQ processor, resolution, and event channel information."""
        return XcpDaqInfo.model_validate(self.call("get_daq_info"))

    def free_daq(self) -> None:
        """Free all DAQ lists."""
        self.call("free_daq")

    def alloc_daq(self, daq_count: int) -> None:
        """Allocate DAQ lists."""
        self.call("alloc_daq", daq_count)

    def alloc_odt(self, daq_list_number: int, odt_count: int) -> None:
        """Allocate ODTs for a DAQ list."""
        self.call("alloc_odt", daq_list_number, odt_count)

    def alloc_odt_entry(
        self, daq_list_number: int, odt_number: int, odt_entries_count: int
    ) -> None:
        """Allocate ODT entries for a specific ODT."""
        self.call("alloc_odt_entry", daq_list_number, odt_number, odt_entries_count)

    def set_daq_ptr(self, daq_list: int, odt: int, entry: int) -> None:
        """Set the DAQ list pointer."""
        self.call("set_daq_ptr", daq_list, odt, entry)

    def write_daq(
        self, bit_offset: int, size: int, ext: int, address: int
    ) -> None:
        """Write a DAQ entry (configure what to measure)."""
        self.call("write_daq", bit_offset, size, ext, address)

    def set_daq_list_mode(
        self, mode: int, daq_list: int, event: int, prescaler: int, priority: int
    ) -> None:
        """Set the mode for a DAQ list."""
        self.call("set_daq_list_mode", mode, daq_list, event, prescaler, priority)

    def start_stop_daq_list(self, mode: int, daq_list: int) -> None:
        """Start or stop a single DAQ list."""
        self.call("start_stop_daq_list", mode, daq_list)

    def start_stop_synch(self, mode: int) -> None:
        """Start or stop all DAQ lists synchronously."""
        self.call("start_stop_synch", mode)

    # --- Programming (Flashing) ---

    def program_start(self) -> XcpProgramInfo:
        """Begin a programming sequence."""
        return XcpProgramInfo.model_validate(self.call("program_start"))

    def program_clear(self, clear_range: int, mode: int = 0) -> None:
        """Clear (erase) a memory range for programming."""
        self.call("program_clear", clear_range, mode)

    def program(self, data: bytes, block_length: int = 0) -> None:
        """Download program data to the slave."""
        self.call("program", data, block_length)

    def program_reset(self) -> None:
        """Reset the slave after programming."""
        self.call("program_reset")
