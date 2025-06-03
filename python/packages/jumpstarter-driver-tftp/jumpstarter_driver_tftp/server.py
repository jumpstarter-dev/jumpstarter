import asyncio
import logging
import pathlib
from enum import IntEnum
from typing import Optional, Set, Tuple

from opendal import AsyncOperator


class Opcode(IntEnum):
    RRQ = 1
    WRQ = 2
    DATA = 3
    ACK = 4
    ERROR = 5
    OACK = 6


class TftpErrorCode(IntEnum):
    NOT_DEFINED = 0
    FILE_NOT_FOUND = 1
    ACCESS_VIOLATION = 2
    DISK_FULL = 3
    ILLEGAL_OPERATION = 4
    UNKNOWN_TID = 5
    FILE_EXISTS = 6
    NO_SUCH_USER = 7


class TftpServer:
    """
    TFTP Server that handles read requests (RRQ).
    """

    def __init__(
        self,
        host: str,
        port: int,
        operator: AsyncOperator,
        block_size: int = 512,
        timeout: float = 5.0,
        retries: int = 3,
        logger: logging.Logger | None = None,
    ):
        self.host = host
        self.port = port
        self.operator = operator
        self.block_size = block_size
        self.timeout = timeout
        self.retries = retries
        self.active_transfers: Set["TftpTransfer"] = set()
        self.shutdown_event = asyncio.Event()
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.protocol: Optional["TftpServerProtocol"] = None

        if logger is not None:
            self.logger = logger.getChild(self.__class__.__name__)
        else:
            self.logger = logging.getLogger(self.__class__.__name__)

        self.ready_event = asyncio.Event()

    @property
    def address(self) -> Optional[Tuple[str, int]]:
        """Get the server's bound address and port."""
        if self.transport:
            return self.transport.get_extra_info("socket").getsockname()
        return None

    async def start(self):
        self.logger.info(f"Starting TFTP server on {self.host}:{self.port}")
        loop = asyncio.get_running_loop()

        self.ready_event.set()
        self.transport, self.protocol = await loop.create_datagram_endpoint(
            lambda: TftpServerProtocol(self), local_addr=(self.host, self.port)
        )

        try:
            await self.shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            self.logger.info("TFTP server shutting down")
            await self._cleanup()

    async def _cleanup(self):
        self.logger.info("Cleaning up TFTP server resources")

        # Cancel all active transfers
        cleanup_tasks = [transfer.cleanup() for transfer in self.active_transfers.copy()]
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)

        # Close the main transport
        if self.transport:
            self.transport.close()
            self.transport = None

        self.logger.info("TFTP server cleanup completed")

    async def shutdown(self):
        self.logger.info("Shutdown signal received for TFTP server")
        self.shutdown_event.set()

    def register_transfer(self, transfer: "TftpTransfer"):
        self.active_transfers.add(transfer)
        self.logger.debug(f"Registered transfer: {transfer}")

    def unregister_transfer(self, transfer: "TftpTransfer"):
        self.active_transfers.discard(transfer)
        self.logger.debug(f"Unregistered transfer: {transfer}")


class TftpServerProtocol(asyncio.DatagramProtocol):
    """
    Protocol for handling incoming TFTP requests.
    """

    def __init__(self, server: TftpServer):
        self.server = server
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.logger = server.logger.getChild(self.__class__.__name__)

    def connection_made(self, transport: asyncio.DatagramTransport):
        self.transport = transport
        self.logger.debug("Server protocol connection established")

    def connection_lost(self, exc: Optional[Exception]):
        self.logger.info("TFTP server protocol connection lost")

    def datagram_received(self, data: bytes, addr: Tuple[str, int]):
        self.logger.debug(f"Received datagram from {addr}")
        if len(data) < 4:
            self.logger.warning(f"Received malformed packet from {addr}")
            self._send_error(addr, TftpErrorCode.ILLEGAL_OPERATION, "Malformed packet")
            return

        if self.server.shutdown_event.is_set():
            self.logger.debug(f"Ignoring packet from {addr} - server is shutting down")
            return

        try:
            opcode = Opcode(int.from_bytes(data[0:2], "big"))
        except ValueError:
            self.logger.error(f"Unknown opcode from {addr}")
            self._send_error(addr, TftpErrorCode.ILLEGAL_OPERATION, "Unknown opcode")
            return

        self.logger.debug(f"Received opcode {opcode.name} from {addr}")

        if opcode == Opcode.RRQ:
            asyncio.create_task(self._handle_read_request(data, addr))
        else:
            self.logger.warning(f"Unsupported opcode {opcode} from {addr}")
            self._send_error(addr, TftpErrorCode.ILLEGAL_OPERATION, "Unsupported operation")

    async def _handle_read_request(self, data: bytes, addr: Tuple[str, int]):
        try:
            filename, mode, options = self._parse_request(data)
            self.logger.info(f"RRQ from {addr}: '{filename}' in mode '{mode}' with options {options}")

            if not self._validate_mode(mode, addr):
                return

            resolved_path = await self._resolve_and_validate_path(filename, addr)
            if not resolved_path:
                return

            negotiated_options, blksize, timeout = self._negotiate_options(options)
            self.logger.info(f"Negotiated options: {negotiated_options}")
            await self._start_transfer(resolved_path, addr, blksize, timeout, negotiated_options)
        except Exception as e:
            self.logger.error(f"Error handling RRQ from {addr}: {e}")
            self._send_error(addr, TftpErrorCode.NOT_DEFINED, str(e))

    def _send_oack(self, addr: Tuple[str, int], options: dict):
        """Send Option Acknowledgment (OACK) packet."""
        oack_data = Opcode.OACK.to_bytes(2, "big")
        for opt_name, opt_value in options.items():
            oack_data += f"{opt_name}\0{str(opt_value)}\0".encode("utf-8")

        if self.transport:
            self.transport.sendto(oack_data, addr)
            self.logger.debug(f"Sent OACK to {addr} with options {options}")

    def _send_error(self, addr: Tuple[str, int], error_code: TftpErrorCode, message: str):
        error_packet = (
            Opcode.ERROR.to_bytes(2, "big") + error_code.to_bytes(2, "big") + message.encode("utf-8") + b"\x00"
        )
        if self.transport:
            self.transport.sendto(error_packet, addr)
            self.logger.debug(f"Sent ERROR {error_code.name} to {addr}: {message}")

    def _parse_request(self, data: bytes) -> Tuple[str, str, dict]:
        parts = data[2:].split(b"\x00")
        if len(parts) < 2:
            raise ValueError("Invalid RRQ format")

        filename = parts[0].decode("utf-8")
        if len(filename) > 255:  # RFC 1350 doesn't specify a limit
            raise ValueError("Filename too long")
        if not all(c.isprintable() and c not in '<>:"/\\|?*' for c in filename):
            raise ValueError("Invalid characters in filename")
        if "\x00" in filename:
            raise ValueError("Null byte in filename")
        mode = parts[1].decode("utf-8").lower()
        options = self._parse_options(parts[2:])

        return filename, mode, options

    def _parse_options(self, option_parts: list) -> dict:
        options = {}
        i = 0
        while i < len(option_parts) - 1:
            try:
                opt_name = option_parts[i].decode("utf-8").lower()
                opt_value = option_parts[i + 1].decode("utf-8")
                options[opt_name] = opt_value
                i += 2
            except Exception:
                break
        return options

    def _validate_mode(self, mode: str, addr: Tuple[str, int]) -> bool:
        if mode not in ("netascii", "octet"):
            self.logger.warning(f"Unsupported transfer mode '{mode}' from {addr}")
            self._send_error(addr, TftpErrorCode.ILLEGAL_OPERATION, "Unsupported transfer mode")
            return False
        return True

    async def _resolve_and_validate_path(self, filename: str, addr: Tuple[str, int]) -> Optional[str]:
        try:
            stat = await self.server.operator.stat(filename)
        except FileNotFoundError:
            self.logger.error(f"File not found: {filename}")
            self._send_error(addr, TftpErrorCode.FILE_NOT_FOUND, "File not found")
            return None

        if not stat.mode.is_file():
            self.logger.error(f"Not a file: {filename}")
            self._send_error(addr, TftpErrorCode.FILE_NOT_FOUND, "Not a file")
            return None

        return filename

    def _negotiate_block_size(self, requested_blksize: Optional[str]) -> int:
        if requested_blksize is None:
            return self.server.block_size

        try:
            blksize = int(requested_blksize)
            if 512 <= blksize <= 65464:
                return blksize
            else:
                self.logger.warning(
                    f"Requested block size {blksize} out of range (512-65464), using default: {self.server.block_size}"
                )
                return self.server.block_size
        except ValueError:
            self.logger.warning(
                f"Invalid block size value '{requested_blksize}', using default: {self.server.block_size}"
            )
            return self.server.block_size

    def _negotiate_timeout(self, requested_timeout: Optional[str]) -> float:
        if requested_timeout is None:
            return self.server.timeout

        try:
            timeout = int(requested_timeout)
            if 1 <= timeout <= 255:
                return float(timeout)
            else:
                self.logger.warning(
                    f"Timeout value {timeout} out of range (1-255), using default: {self.server.timeout}"
                )
                return self.server.timeout
        except ValueError:
            self.logger.warning(f"Invalid timeout value '{requested_timeout}', using default: {self.server.timeout}")
            return self.server.timeout

    def _negotiate_options(self, options: dict) -> Tuple[dict, int, float]:
        negotiated = {}
        blksize = self.server.block_size
        timeout = self.server.timeout

        if "blksize" in options:
            requested = options["blksize"]
            blksize = self._negotiate_block_size(requested)
            negotiated["blksize"] = blksize

        if "timeout" in options:
            requested = options["timeout"]
            timeout = self._negotiate_timeout(requested)
            negotiated["timeout"] = int(timeout)

        return negotiated, blksize, timeout

    async def _start_transfer(
        self, filepath: str, addr: Tuple[str, int], blksize: int, timeout: float, negotiated_options: dict
    ):
        transfer = TftpReadTransfer(
            server=self.server,
            filepath=filepath,
            client_addr=addr,
            block_size=blksize,
            timeout=timeout,
            retries=self.server.retries,
            negotiated_options=negotiated_options,
        )
        self.server.register_transfer(transfer)
        asyncio.create_task(transfer.start())


def is_subpath(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class TftpTransfer:
    """
    Base class for TFTP transfers.
    """

    def __init__(
        self,
        server: TftpServer,
        filepath: pathlib.Path,
        client_addr: Tuple[str, int],
        block_size: int,
        timeout: float,
        retries: int,
    ):
        self.server = server
        self.filepath = filepath
        self.client_addr = client_addr
        self.block_size = block_size
        self.timeout = timeout
        self.retries = retries
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.protocol: Optional["TftpTransferProtocol"] = None
        self.cleanup_task: Optional[asyncio.Task] = None
        self.logger = server.logger.getChild(self.__class__.__name__)

    async def start(self):
        """Start the transfer."""
        raise NotImplementedError

    async def cleanup(self):
        """Clean up transfer resources."""
        self.logger.info(f"Cleaning up transfer for {self.client_addr}")
        if self.transport:
            self.transport.close()
            self.transport = None
        self.server.unregister_transfer(self)


class TftpReadTransfer(TftpTransfer):
    def __init__(
        self,
        server: TftpServer,
        filepath: str,
        client_addr: Tuple[str, int],
        block_size: int,
        timeout: float,
        retries: int,
        negotiated_options: Optional[dict] = None,
    ):
        super().__init__(
            server=server,
            filepath=filepath,
            client_addr=client_addr,
            block_size=block_size,
            timeout=timeout,
            retries=retries,
        )
        self.block_num = 0
        self.ack_received = asyncio.Event()
        self.last_ack = 0
        self.oack_confirmed = False
        self.negotiated_options = negotiated_options
        self.current_packet: Optional[bytes] = None

    async def start(self):
        self.logger.info(f"Starting read transfer of '{self.filepath}' to {self.client_addr}")

        if not await self._initialize_transfer():
            return

        try:
            # if no options were negotiated, we can start sending data immediately
            if not self.negotiated_options:
                self.oack_confirmed = True

            await self._perform_transfer()
        except Exception as e:
            self.logger.error(f"Error during read transfer: {e}")
        finally:
            await self.cleanup()

    async def _initialize_transfer(self) -> bool:
        loop = asyncio.get_running_loop()

        self.transport, self.protocol = await loop.create_datagram_endpoint(
            lambda: TftpTransferProtocol(self), local_addr=("0.0.0.0", 0), remote_addr=self.client_addr
        )
        local_addr = self.transport.get_extra_info("sockname")
        self.logger.debug(f"Transfer bound to local {local_addr}")

        # Only send OACK if we have non-default options to negotiate
        if self.negotiated_options and (
            self.negotiated_options["blksize"] != 512 or self.negotiated_options["timeout"] != self.server.timeout
        ):
            oack_packet = self._create_oack_packet()
            if not await self._send_with_retries(oack_packet, is_oack=True):
                self.logger.error("Failed to get acknowledgment for OACK")
                return False

        self.block_num = 1
        return True

    async def _perform_transfer(self):
        async with await self.server.operator.open(self.filepath, "rb") as f:
            while True:
                if self.server.shutdown_event.is_set():
                    self.logger.info(f"Server shutdown detected, stopping transfer to {self.client_addr}")
                    break

                # read a full block or until EOF
                data = bytearray()
                while len(data) < self.block_size:
                    chunk = await f.read(size=self.block_size - len(data))
                    if not chunk:  # EOF reached
                        break
                    data.extend(chunk)

                # send the data (converted to bytes)
                if not await self._handle_data_block(bytes(data)):
                    break

    async def _handle_data_block(self, data: bytes) -> bool:
        """
        Handle sending a block of data to the client.
        Returns False if transfer should stop, True if it should continue.
        """
        if not data and self.block_num == 1:
            # Empty file case
            packet = self._create_data_packet(b"")
            await self._send_with_retries(packet)
            return False
        elif data:
            packet = self._create_data_packet(data)
            success = await self._send_with_retries(packet)
            if not success:
                self.logger.error(f"Failed to send block {self.block_num} to {self.client_addr}")
                return False

            self.logger.debug(f"Block {self.block_num} sent successfully")
            self.block_num += 1

            # wrap block number around if it exceeds 16 bits
            self.block_num %= 65536

            if len(data) < self.block_size:
                self.logger.info(f"Final block {self.block_num - 1} sent")
                return False
            return True
        else:
            # EOF reached
            packet = self._create_data_packet(b"")
            success = await self._send_with_retries(packet)
            if not success:
                self.logger.error(f"Failed to send final block {self.block_num}")
            else:
                self.logger.info(f"Transfer complete, final block {self.block_num}")
            return False

    def _create_oack_packet(self) -> bytes:
        packet = Opcode.OACK.to_bytes(2, "big")
        for opt_name, opt_value in self.negotiated_options.items():
            packet += f"{opt_name}\0{str(opt_value)}\0".encode("utf-8")
        return packet

    def _create_data_packet(self, data: bytes) -> bytes:
        return Opcode.DATA.to_bytes(2, "big") + self.block_num.to_bytes(2, "big") + data

    def _send_packet(self, packet: bytes):
        self.transport.sendto(packet)
        if packet[0:2] == Opcode.DATA.to_bytes(2, "big"):
            block = int.from_bytes(packet[2:4], "big")
            data_length = len(packet) - 4
            self.logger.debug(f"Sent DATA block {block} ({data_length} bytes) to {self.client_addr}")
        elif packet[0:2] == Opcode.OACK.to_bytes(2, "big"):
            self.logger.debug(f"Sent OACK to {self.client_addr}")

    async def _send_with_retries(self, packet: bytes, is_oack: bool = False) -> bool:
        self.current_packet = packet
        expected_block = 0 if is_oack else self.block_num

        for attempt in range(1, self.retries + 1):
            try:
                self._send_packet(packet)
                self.logger.debug(
                    f"Sent {'OACK' if is_oack else 'DATA'} block {expected_block}, waiting for ACK (Attempt {attempt})"
                )
                self.ack_received.clear()
                await asyncio.wait_for(self.ack_received.wait(), timeout=self.timeout)

                if self.last_ack == expected_block:
                    self.logger.debug(f"ACK received for block {expected_block}")
                    return True
                else:
                    self.logger.warning(f"Received wrong ACK: expected {expected_block}, got {self.last_ack}")

            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout waiting for ACK of block {expected_block} (Attempt {attempt})")

        return False

    def handle_ack(self, block_num: int):
        self.logger.debug(f"Received ACK for block {block_num} from {self.client_addr}")

        # special handling for OACK acknowledgment
        if not self.oack_confirmed and self.negotiated_options and block_num == 0:
            self.oack_confirmed = True
            self.last_ack = block_num
            self.ack_received.set()
            return

        if block_num == self.block_num:
            self.last_ack = block_num
            self.ack_received.set()
        elif block_num == self.block_num - 1:
            self.logger.warning(f"Duplicate ACK for block {block_num} received, resending block {self.block_num}")
            self.transport.sendto(self.current_packet)
        else:
            self.logger.warning(f"Out of sequence ACK: expected {self.block_num}, got {block_num}")


class TftpTransferProtocol(asyncio.DatagramProtocol):
    """
    Protocol for handling ACKs during a TFTP transfer.
    """

    def __init__(self, transfer: TftpReadTransfer):
        self.transfer = transfer
        self.logger = transfer.logger.getChild(self.__class__.__name__)

    def connection_made(self, transport: asyncio.DatagramTransport):
        self.transfer.transport = transport
        local_addr = transport.get_extra_info("sockname")
        self.logger.debug(f"Transfer protocol connection established on {local_addr} for {self.transfer.client_addr}")

    def datagram_received(self, data: bytes, addr: Tuple[str, int]):
        self.logger.debug(f"Received datagram from {addr}")
        if addr != self.transfer.client_addr:
            self.logger.warning(f"Ignoring packet from unknown source {addr}")
            return

        if len(data) < 4:
            self.logger.warning(f"Received malformed packet from {addr}")
            return

        try:
            opcode = Opcode(int.from_bytes(data[0:2], "big"))
        except ValueError:
            self.logger.error(f"Unknown opcode from {addr}")
            self._send_error(addr, TftpErrorCode.ILLEGAL_OPERATION, "Unknown opcode")
            return

        if opcode == Opcode.ACK:
            block_num = int.from_bytes(data[2:4], "big")
            self.logger.debug(f"Received ACK for block {block_num} from {addr}")
            self.transfer.handle_ack(block_num)
        else:
            self.logger.warning(f"Unexpected opcode {opcode} from {addr}")
            self._send_error(addr, TftpErrorCode.ILLEGAL_OPERATION, "Unexpected opcode")

    def error_received(self, exc):
        self.logger.error(f"Error received: {exc}")

    def connection_lost(self, exc):
        self.logger.debug(f"Connection closed for transfer to {self.transfer.client_addr}")

    def _send_error(self, addr: Tuple[str, int], error_code: TftpErrorCode, message: str):
        error_packet = (
            Opcode.ERROR.to_bytes(2, "big") + error_code.to_bytes(2, "big") + message.encode("utf-8") + b"\x00"
        )
        if self.transfer.transport:
            self.transfer.transport.sendto(error_packet)
            self.logger.debug(f"Sent ERROR {error_code.name} to {addr}: {message}")
