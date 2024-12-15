import asyncio
import logging
import os
import pathlib
from enum import IntEnum
from typing import Optional, Set, Tuple

import aiofiles


class Opcode(IntEnum):
    RRQ = 1
    WRQ = 2
    DATA = 3
    ACK = 4
    ERROR = 5


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

    def __init__(self, host: str, port: int, root_dir: str,
                 block_size: int = 512, timeout: float = 5.0, retries: int = 3):
        self.host = host
        self.port = port
        self.root_dir = pathlib.Path(os.path.abspath(root_dir))
        self.block_size = block_size
        self.timeout = timeout
        self.retries = retries
        self.active_transfers: Set['TftpTransfer'] = set()
        self.shutdown_event = asyncio.Event()
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.protocol: Optional['TftpServerProtocol'] = None
        self.logger = logging.getLogger(self.__class__.__name__)

    async def start(self):
        self.logger.info(f"Starting TFTP server on {self.host}:{self.port}")
        loop = asyncio.get_running_loop()

        self.transport, self.protocol = await loop.create_datagram_endpoint(
            lambda: TftpServerProtocol(self),
            local_addr=(self.host, self.port)
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

    def register_transfer(self, transfer: 'TftpTransfer'):
        self.active_transfers.add(transfer)
        self.logger.debug(f"Registered transfer: {transfer}")

    def unregister_transfer(self, transfer: 'TftpTransfer'):
        self.active_transfers.discard(transfer)
        self.logger.debug(f"Unregistered transfer: {transfer}")


class TftpServerProtocol(asyncio.DatagramProtocol):
    """
    Protocol for handling incoming TFTP requests.
    """

    def __init__(self, server: TftpServer):
        self.server = server
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.logger = logging.getLogger(self.__class__.__name__)

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
            opcode = Opcode(int.from_bytes(data[0:2], 'big'))
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
            # Parse filename and mode from request
            parts = data[2:].split(b'\x00')
            if len(parts) < 2:
                self.logger.error(f"Invalid RRQ format from {addr}")
                raise ValueError("Invalid RRQ format")

            filename = parts[0].decode('utf-8')
            mode = parts[1].decode('utf-8').lower()

            self.logger.info(f"RRQ from {addr}: '{filename}' in mode '{mode}'")

            if mode not in ('netascii', 'octet'):
                self.logger.warning(f"Unsupported transfer mode '{mode}' from {addr}")
                self._send_error(addr, TftpErrorCode.ILLEGAL_OPERATION, "Unsupported transfer mode")
                return

            # Resolve file path securely
            requested_path = self.server.root_dir / filename
            resolved_path = requested_path.resolve()

            if not resolved_path.is_file():
                self.logger.error(f"File not found: {resolved_path}")
                self._send_error(addr, TftpErrorCode.FILE_NOT_FOUND, "File not found")
                return

            if not is_subpath(resolved_path, self.server.root_dir):
                self.logger.error(f"Access violation: {resolved_path} is outside the root directory")
                self._send_error(addr, TftpErrorCode.ACCESS_VIOLATION, "Access denied")
                return

            transfer = TftpReadTransfer(
                server=self.server,
                filepath=resolved_path,
                client_addr=addr,
                block_size=self.server.block_size,
                timeout=self.server.timeout,
                retries=self.server.retries
            )
            self.server.register_transfer(transfer)
            asyncio.create_task(transfer.start())

        except Exception as e:
            self.logger.error(f"Error handling RRQ from {addr}: {e}")
            self._send_error(addr, TftpErrorCode.NOT_DEFINED, str(e))

    def _send_error(self, addr: Tuple[str, int], error_code: TftpErrorCode, message: str):
        error_packet = (
            Opcode.ERROR.to_bytes(2, 'big') +
            error_code.to_bytes(2, 'big') +
            message.encode('utf-8') + b'\x00'
        )
        if self.transport:
            self.transport.sendto(error_packet, addr)
            self.logger.debug(f"Sent ERROR {error_code.name} to {addr}: {message}")


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

    def __init__(self, server: TftpServer, filepath: pathlib.Path, client_addr: Tuple[str, int],
                 block_size: int, timeout: float, retries: int):
        self.server = server
        self.filepath = filepath
        self.client_addr = client_addr
        self.block_size = block_size
        self.timeout = timeout
        self.retries = retries
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.protocol: Optional['TftpTransferProtocol'] = None
        self.cleanup_task: Optional[asyncio.Task] = None
        self.logger = logging.getLogger(self.__class__.__name__)

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
    """
    Handles a TFTP Read (RRQ) transfer.
    """

    def __init__(self, server: TftpServer, filepath: pathlib.Path, client_addr: Tuple[str, int],
                 block_size: int, timeout: float, retries: int):
        super().__init__(server, filepath, client_addr, block_size, timeout, retries)
        self.block_num = 1
        self.ack_received = asyncio.Event()
        self.last_ack = 0

    async def start(self):
        self.logger.info(f"Starting read transfer of '{self.filepath.name}' to {self.client_addr}")
        loop = asyncio.get_running_loop()

        self.transport, self.protocol = await loop.create_datagram_endpoint(
            lambda: TftpTransferProtocol(self),
            local_addr=('0.0.0.0', 0),
            remote_addr=self.client_addr
        )
        local_addr = self.transport.get_extra_info('sockname')
        self.logger.debug(f"Transfer bound to local {local_addr}")

        try:
            async with aiofiles.open(self.filepath, 'rb') as f:
                while True:
                    if self.server.shutdown_event.is_set():
                        self.logger.info(f"Server shutdown detected, stopping transfer to {self.client_addr}")
                        break
                    data = await f.read(self.block_size)
                    if data:
                        packet = (
                            Opcode.DATA.to_bytes(2, 'big') +
                            self.block_num.to_bytes(2, 'big') +
                            data
                        )
                        success = await self._send_with_retries(packet)
                        if not success:
                            self.logger.error(f"Failed to send block {self.block_num} to {self.client_addr}")
                            break
                        self.logger.debug(f"Block {self.block_num} sent successfully")
                        self.block_num += 1

                        # If the data read is less than block_size, this is the last packet
                        if len(data) < self.block_size:
                            self.logger.info(f"Final block {self.block_num - 1} reached for {self.client_addr}")
                            break
                    else:
                        # If no data is returned, it means the file size is an exact multiple of block_size
                        # Send an extra empty DATA packet to signal end of transfer
                        packet = (
                            Opcode.DATA.to_bytes(2, 'big') +
                            self.block_num.to_bytes(2, 'big') +
                            b''
                        )
                        success = await self._send_with_retries(packet)
                        if not success:
                            self.logger.error(
                                f"Failed to send final empty block {self.block_num} "
                                f"to {self.client_addr}"
                            )
                            break
                        self.logger.info(f"Transfer complete to {self.client_addr}, final block {self.block_num}")
                        break

        except Exception as e:
            self.logger.error(f"Error during read transfer: {e}")
        finally:
            await self.cleanup()

    async def _send_with_retries(self, packet: bytes) -> bool:
        self.current_packet = packet
        for attempt in range(1, self.retries + 1):
            try:
                self._send_packet(packet)
                self.logger.debug(f"Sent DATA block {self.block_num}, waiting for ACK (Attempt {attempt})")
                self.ack_received.clear()
                await asyncio.wait_for(self.ack_received.wait(), timeout=self.timeout)

                if self.last_ack == self.block_num:
                    self.logger.debug(f"ACK received for block {self.block_num}")
                    return True
                else:
                    self.logger.warning(f"Received wrong ACK: expected {self.block_num}, got {self.last_ack}")

            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout waiting for ACK of block {self.block_num} (Attempt {attempt})")

        return False

    def _send_packet(self, packet: bytes):
        """
        Sends a DATA packet to the client.
        """
        self.transport.sendto(packet)
        block = int.from_bytes(packet[2:4], 'big')
        data_length = len(packet) - 4
        self.logger.debug(f"Sent DATA block {block} ({data_length} bytes) to {self.client_addr}")

    def handle_ack(self, block_num: int):
        self.logger.debug(f"Received ACK for block {block_num} from {self.client_addr}")
        if block_num == self.block_num:
            self.last_ack = block_num
            self.ack_received.set()
        elif block_num == self.block_num - 1:
            # Duplicate ACK for previous block, resend current packet
            self.logger.warning(f"Duplicate ACK for block {block_num} received, resending DATA block {self.block_num}")
            self.transport.sendto(self.current_packet)
        else:
            self.logger.warning(f"Out of sequence ACK: expected {self.block_num}, got {block_num}")

class TftpTransferProtocol(asyncio.DatagramProtocol):
    """
    Protocol for handling ACKs during a TFTP transfer.
    """

    def __init__(self, transfer: TftpReadTransfer):
        self.transfer = transfer
        self.logger = logging.getLogger(self.__class__.__name__)

    def connection_made(self, transport: asyncio.DatagramTransport):
        self.transfer.transport = transport
        local_addr = transport.get_extra_info('sockname')
        self.logger.debug(f"Transfer protocol connection established on {local_addr} for {self.transfer.client_addr}")

    def datagram_received(self, data: bytes, addr: Tuple[str, int]):
        self.logger.debug(f"Received datagram from {addr}")
        if addr != self.transfer.client_addr:
            self.logger.warning(f"Ignoring packet from unknown source {addr}")
            return

        if len(data) < 4:
            self.logger.warning(f"Received malformed ACK from {addr}")
            return

        try:
            opcode = Opcode(int.from_bytes(data[0:2], 'big'))
        except ValueError:
            self.logger.error(f"Unknown opcode in ACK from {addr}")
            self._send_error(addr, TftpErrorCode.ILLEGAL_OPERATION, "Unknown opcode in ACK")
            return

        if opcode != Opcode.ACK:
            self.logger.warning(f"Expected ACK but got opcode {opcode} from {addr}")
            self._send_error(addr, TftpErrorCode.ILLEGAL_OPERATION, "Expected ACK")
            return

        block_num = int.from_bytes(data[2:4], 'big')
        self.logger.debug(f"Received ACK for block {block_num} from {addr}")
        self.transfer.handle_ack(block_num)

    def error_received(self, exc):
        self.logger.error(f"Error received: {exc}")

    def connection_lost(self, exc):
        self.logger.debug(f"Connection closed for transfer to {self.transfer.client_addr}")

    def _send_error(self, addr: Tuple[str, int], error_code: TftpErrorCode, message: str):
        error_packet = (
            Opcode.ERROR.to_bytes(2, 'big') +
            error_code.to_bytes(2, 'big') +
            message.encode('utf-8') + b'\x00'
        )
        if self.transfer.transport:
            self.transfer.transport.sendto(error_packet)
            self.logger.debug(f"Sent ERROR {error_code.name} to {addr}: {message}")
