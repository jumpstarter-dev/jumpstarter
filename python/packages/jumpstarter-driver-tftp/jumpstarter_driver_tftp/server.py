import logging
import math
import pathlib
from enum import IntEnum
from typing import Any

from anyio import (
    TASK_STATUS_IGNORED,
    Event,
    create_connected_udp_socket,
    create_memory_object_stream,
    create_task_group,
    create_udp_socket,
    fail_after,
)
from anyio.abc import ConnectedUDPSocket, SocketAttribute, TaskGroup, TaskStatus, UDPSocket
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


class DatagramSender:
    """Sends datagrams for synchronous callers, as asyncio's transport.sendto did.

    anyio sockets take one send at a time, so sends queue here and run() sends
    them in order; a send that fails is logged, as a datagram that got lost.
    """

    def __init__(self, sock: UDPSocket | ConnectedUDPSocket, logger: logging.Logger):
        self._sock: Any = sock
        self._logger = logger
        self._queue, self._pending = create_memory_object_stream[tuple[bytes, Any]](math.inf)

    def sendto(self, data: bytes, addr: tuple[str, int] | None = None) -> None:
        self._queue.send_nowait((data, addr))

    async def run(self) -> None:
        async for data, addr in self._pending:
            try:
                if addr is None:
                    await self._sock.send(data)
                else:
                    await self._sock.sendto(data, *addr)
            except Exception:
                self._logger.exception(f"Failed to send a datagram to {addr or 'the client'}")


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
        self.active_transfers: set[TftpTransfer] = set()
        self.shutdown_event = Event()
        self.sock: UDPSocket | None = None
        self.protocol: TftpServerProtocol | None = None

        if logger is not None:
            self.logger = logger.getChild(self.__class__.__name__)
        else:
            self.logger = logging.getLogger(self.__class__.__name__)

        self.ready_event = Event()

    @property
    def address(self) -> tuple[str, int] | None:
        """Get the server's bound address and port."""
        if self.sock:
            return self.sock.extra(SocketAttribute.raw_socket).getsockname()
        return None

    async def start(self, *, task_status: TaskStatus[None] = TASK_STATUS_IGNORED):
        self.logger.info(f"Starting TFTP server on {self.host}:{self.port}")
        async with await create_udp_socket(local_host=self.host, local_port=self.port) as sock:
            self.sock = sock
            try:
                async with create_task_group() as tg:
                    self.protocol = TftpServerProtocol(self, sock, tg)
                    tg.start_soon(self.protocol.receive)
                    self.ready_event.set()
                    task_status.started()
                    await self.shutdown_event.wait()
                    self.logger.info("TFTP server shutting down")
                    tg.cancel_scope.cancel()
            finally:
                self.sock = None
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


class TftpServerProtocol:
    """
    Protocol for handling incoming TFTP requests.
    """

    def __init__(self, server: TftpServer, sock: UDPSocket, tg: TaskGroup):
        self.server = server
        self.sock = sock
        self.tg = tg
        self.logger = server.logger.getChild(self.__class__.__name__)
        self.transport = DatagramSender(sock, self.logger)
        tg.start_soon(self.transport.run)

    async def receive(self):
        async for data, addr in self.sock:
            self.datagram_received(data, addr)

    def datagram_received(self, data: bytes, addr: tuple[str, int]):
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
            self.tg.start_soon(self._handle_read_request, data, addr)
        else:
            self.logger.warning(f"Unsupported opcode {opcode} from {addr}")
            self._send_error(addr, TftpErrorCode.ILLEGAL_OPERATION, "Unsupported operation")

    async def _handle_read_request(self, data: bytes, addr: tuple[str, int]):
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
        except Exception as e:  # pragma: no cover  # noqa: BLE001
            self.logger.error(f"Error handling RRQ from {addr}: {e}")
            self._send_error(addr, TftpErrorCode.NOT_DEFINED, str(e))

    def _send_oack(self, addr: tuple[str, int], options: dict):
        """Send Option Acknowledgment (OACK) packet."""
        oack_data = Opcode.OACK.to_bytes(2, "big")
        for opt_name, opt_value in options.items():
            oack_data += f"{opt_name}\0{opt_value!s}\0".encode()

        if self.transport:
            self.transport.sendto(oack_data, addr)
            self.logger.debug(f"Sent OACK to {addr} with options {options}")

    def _send_error(self, addr: tuple[str, int], error_code: TftpErrorCode, message: str):
        error_packet = (
            Opcode.ERROR.to_bytes(2, "big") + error_code.to_bytes(2, "big") + message.encode("utf-8") + b"\x00"
        )
        if self.transport:
            self.transport.sendto(error_packet, addr)
            self.logger.debug(f"Sent ERROR {error_code.name} to {addr}: {message}")

    def _parse_request(self, data: bytes) -> tuple[str, str, dict]:
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
            except Exception:  # pragma: no cover  # noqa: BLE001
                break
        return options

    def _validate_mode(self, mode: str, addr: tuple[str, int]) -> bool:
        if mode not in ("netascii", "octet"):
            self.logger.warning(f"Unsupported transfer mode '{mode}' from {addr}")
            self._send_error(addr, TftpErrorCode.ILLEGAL_OPERATION, "Unsupported transfer mode")
            return False
        return True

    async def _resolve_and_validate_path(self, filename: str, addr: tuple[str, int]) -> str | None:
        normalized = pathlib.PurePosixPath(filename)
        if ".." in normalized.parts or normalized.is_absolute():
            self.logger.error(f"Path traversal attempt from {addr}: {filename}")
            self._send_error(addr, TftpErrorCode.ACCESS_VIOLATION, "Access violation")
            return None

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

    def _negotiate_block_size(self, requested_blksize: str | None) -> int:
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

    def _negotiate_timeout(self, requested_timeout: str | None) -> float:
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

    def _negotiate_options(self, options: dict) -> tuple[dict, int, float]:
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
        self, filepath: str, addr: tuple[str, int], blksize: int, timeout: float, negotiated_options: dict
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
        self.tg.start_soon(transfer.start)



class TftpTransfer:
    """
    Base class for TFTP transfers.
    """

    def __init__(
        self,
        server: TftpServer,
        filepath: pathlib.Path,
        client_addr: tuple[str, int],
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
        self.sock: ConnectedUDPSocket | None = None
        self.transport: DatagramSender | None = None
        self.protocol: TftpTransferProtocol | None = None
        self.logger = server.logger.getChild(self.__class__.__name__)

    async def start(self):
        """Start the transfer."""
        raise NotImplementedError

    async def cleanup(self):
        """Clean up transfer resources."""
        self.logger.info(f"Cleaning up transfer for {self.client_addr}")
        if self.sock:
            await self.sock.aclose()
            self.sock = None
            self.transport = None
        self.server.unregister_transfer(self)


class TftpReadTransfer(TftpTransfer):
    def __init__(
        self,
        server: TftpServer,
        filepath: str,
        client_addr: tuple[str, int],
        block_size: int,
        timeout: float,
        retries: int,
        negotiated_options: dict | None = None,
    ):
        super().__init__(
            server=server,
            filepath=filepath,  # ty: ignore[invalid-argument-type]
            client_addr=client_addr,
            block_size=block_size,
            timeout=timeout,
            retries=retries,
        )
        self.block_num = 0
        self.ack_received = Event()
        self.last_ack = 0
        self.oack_confirmed = False
        self.negotiated_options = negotiated_options
        self.current_packet: bytes | None = None

    async def start(self):
        self.logger.info(f"Starting read transfer of '{self.filepath}' to {self.client_addr}")

        async with create_task_group() as tg:
            try:
                if not await self._initialize_transfer(tg):
                    return

                # if no options were negotiated, we can start sending data immediately
                if not self.negotiated_options:
                    self.oack_confirmed = True

                await self._perform_transfer()
            except Exception as e:  # noqa: BLE001
                self.logger.error(f"Error during read transfer: {e}")
            finally:
                await self.cleanup()
                tg.cancel_scope.cancel()

    async def _initialize_transfer(self, tg: TaskGroup) -> bool:
        self.sock = await create_connected_udp_socket(*self.client_addr, local_host="0.0.0.0")
        self.transport = DatagramSender(self.sock, self.logger)
        tg.start_soon(self.transport.run)
        self.protocol = TftpTransferProtocol(self)
        tg.start_soon(self.protocol.receive, self.sock)
        local_addr = self.sock.extra(SocketAttribute.local_address)
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
        async with await self.server.operator.open(self.filepath, "rb") as f:  # ty: ignore[invalid-context-manager]
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
        assert self.negotiated_options is not None  # pragma: no cover
        for opt_name, opt_value in self.negotiated_options.items():
            packet += f"{opt_name}\0{opt_value!s}\0".encode()
        return packet

    def _create_data_packet(self, data: bytes) -> bytes:
        return Opcode.DATA.to_bytes(2, "big") + self.block_num.to_bytes(2, "big") + data

    def _send_packet(self, packet: bytes):
        assert self.transport is not None
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
                self.ack_received = Event()
                with fail_after(self.timeout):
                    await self.ack_received.wait()

                if self.last_ack == expected_block:
                    self.logger.debug(f"ACK received for block {expected_block}")
                    return True
                else:
                    self.logger.warning(f"Received wrong ACK: expected {expected_block}, got {self.last_ack}")

            except TimeoutError:
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
            assert self.transport is not None and self.current_packet is not None  # pragma: no cover
            self.transport.sendto(self.current_packet)
        else:
            self.logger.warning(f"Out of sequence ACK: expected {self.block_num}, got {block_num}")


class TftpTransferProtocol:
    """
    Protocol for handling ACKs during a TFTP transfer.
    """

    def __init__(self, transfer: TftpReadTransfer):
        self.transfer = transfer
        self.logger = transfer.logger.getChild(self.__class__.__name__)

    async def receive(self, sock: ConnectedUDPSocket):
        async for data in sock:
            self.datagram_received(data, self.transfer.client_addr)

    def datagram_received(self, data: bytes, addr: tuple[str, int]):
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

    def _send_error(self, addr: tuple[str, int], error_code: TftpErrorCode, message: str):
        error_packet = (
            Opcode.ERROR.to_bytes(2, "big") + error_code.to_bytes(2, "big") + message.encode("utf-8") + b"\x00"
        )
        if self.transfer.transport:
            self.transfer.transport.sendto(error_packet)
            self.logger.debug(f"Sent ERROR {error_code.name} to {addr}: {message}")
