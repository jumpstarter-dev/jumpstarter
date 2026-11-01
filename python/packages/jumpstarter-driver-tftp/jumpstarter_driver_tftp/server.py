import logging
import pathlib
from enum import IntEnum

from anyio import TASK_STATUS_IGNORED, create_connected_udp_socket, create_task_group, create_udp_socket, move_on_after
from anyio.abc import ConnectedUDPSocket, IPSockAddrType, SocketAttribute, TaskStatus, UDPSocket
from opendal import AsyncOperator
from opendal.exceptions import NotFound


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


_OPTION_RANGES = {"blksize": (512, 65464), "timeout": (1, 255)}


class TftpRequestError(Exception):
    """A request the server refuses with an ERROR packet."""

    def __init__(self, code: TftpErrorCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def packet(self) -> bytes:
        return Opcode.ERROR.to_bytes(2, "big") + self.code.to_bytes(2, "big") + self.message.encode() + b"\0"


def _opcode(packet: bytes) -> Opcode | None:
    try:
        return Opcode(int.from_bytes(packet[:2], "big"))
    except ValueError:
        return None


def _ack(block: int) -> bytes:
    return Opcode.ACK.to_bytes(2, "big") + block.to_bytes(2, "big")


def _data(block: int, data: bytes) -> bytes:
    return Opcode.DATA.to_bytes(2, "big") + block.to_bytes(2, "big") + data


def _oack(options: dict[str, int]) -> bytes:
    return Opcode.OACK.to_bytes(2, "big") + b"".join(f"{name}\0{value}\0".encode() for name, value in options.items())


def _error_message(packet: bytes) -> str:
    return packet[4:].split(b"\0", 1)[0].decode(errors="replace")


def _in_range(value: str, low: int, high: int) -> int | None:
    try:
        number = int(value)
    except ValueError:
        return None
    return number if low <= number <= high else None


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

        if logger is not None:
            self.logger = logger.getChild(self.__class__.__name__)
        else:
            self.logger = logging.getLogger(self.__class__.__name__)

    async def serve(self, *, task_status: TaskStatus[int] = TASK_STATUS_IGNORED) -> None:
        """Answer requests until cancelled, which also ends the transfers in progress.

        ``task_status.started()`` receives the bound port once the server listens.
        """
        async with await create_udp_socket(local_host=self.host, local_port=self.port) as sock:
            port = sock.extra(SocketAttribute.local_port)
            self.logger.info(f"TFTP server listening on {self.host}:{port}")
            async with create_task_group() as tg:
                task_status.started(port)
                async for packet, client in sock:
                    tg.start_soon(self._handle_packet, sock, packet, client)

    async def _handle_packet(self, sock: UDPSocket, packet: bytes, client: IPSockAddrType) -> None:
        if _opcode(packet) == Opcode.ERROR:
            return
        try:
            filename, mode, options = self._parse_request(packet)
            self.logger.info(f"RRQ from {client}: '{filename}' in mode '{mode}' with options {options}")
            self._validate_mode(mode)
            await self._resolve_and_validate_path(filename)
            await TftpReadTransfer(self, client, filename, self._negotiate_options(options)).run()
        except TftpRequestError as e:
            self.logger.warning(f"Refused request from {client}: {e.message}")
            await sock.sendto(e.packet(), *client)
        except Exception:
            self.logger.exception(f"Failed to serve request from {client}")

    def _parse_request(self, packet: bytes) -> tuple[str, str, dict[str, str]]:
        if len(packet) < 4:
            raise TftpRequestError(TftpErrorCode.ILLEGAL_OPERATION, "Malformed packet")
        opcode = _opcode(packet)
        if opcode is None:
            raise TftpRequestError(TftpErrorCode.ILLEGAL_OPERATION, "Unknown opcode")
        if opcode != Opcode.RRQ:
            raise TftpRequestError(TftpErrorCode.ILLEGAL_OPERATION, "Unsupported operation")

        parts = packet[2:].split(b"\0")
        if len(parts) < 2:
            raise TftpRequestError(TftpErrorCode.NOT_DEFINED, "Invalid RRQ format")
        try:
            filename = parts[0].decode()
            mode = parts[1].decode().lower()
        except UnicodeDecodeError:
            raise TftpRequestError(TftpErrorCode.NOT_DEFINED, "Invalid RRQ format") from None
        if len(filename) > 255:  # RFC 1350 doesn't specify a limit
            raise TftpRequestError(TftpErrorCode.NOT_DEFINED, "Filename too long")
        if not all(c.isprintable() and c not in '<>:"/\\|?*' for c in filename):
            raise TftpRequestError(TftpErrorCode.NOT_DEFINED, "Invalid characters in filename")
        return filename, mode, self._parse_options(parts[2:])

    def _parse_options(self, parts: list[bytes]) -> dict[str, str]:
        options = {}
        for name, value in zip(parts[0::2], parts[1::2], strict=False):
            try:
                options[name.decode().lower()] = value.decode()
            except UnicodeDecodeError:
                break
        return options

    def _validate_mode(self, mode: str) -> None:
        if mode not in ("netascii", "octet"):
            raise TftpRequestError(TftpErrorCode.ILLEGAL_OPERATION, "Unsupported transfer mode")

    async def _resolve_and_validate_path(self, filename: str) -> str:
        normalized = pathlib.PurePosixPath(filename)
        if ".." in normalized.parts or normalized.is_absolute():
            raise TftpRequestError(TftpErrorCode.ACCESS_VIOLATION, "Access violation")

        try:
            stat = await self.operator.stat(filename)
        except NotFound:
            raise TftpRequestError(TftpErrorCode.FILE_NOT_FOUND, "File not found") from None

        if not stat.mode.is_file():
            raise TftpRequestError(TftpErrorCode.FILE_NOT_FOUND, "Not a file")
        return filename

    def _negotiate_options(self, requested: dict[str, str]) -> dict[str, int]:
        accepted = {}
        for name, (low, high) in _OPTION_RANGES.items():
            if name not in requested:
                continue
            number = _in_range(requested[name], low, high)
            if number is None:
                self.logger.warning(f"Ignoring option {name}={requested[name]!r}, accepted range is {low}-{high}")
            else:
                accepted[name] = number
        return accepted


class TftpReadTransfer:
    """Sends one file to one client, from a port of its own as RFC 1350 requires."""

    def __init__(self, server: TftpServer, client: IPSockAddrType, filename: str, options: dict[str, int]):
        self.server = server
        self.client = client
        self.filename = filename
        self.options = options
        self.block_size = options.get("blksize", server.block_size)
        self.timeout = options.get("timeout", server.timeout)
        self.logger = server.logger.getChild(self.__class__.__name__)

    async def run(self) -> None:
        async with await create_connected_udp_socket(*self.client, local_host=self.server.host) as sock:
            if self.options and not await self._send_until_acked(sock, _oack(self.options), 0):
                return
            async with await self.server.operator.open(self.filename, "rb") as file:  # ty: ignore[invalid-context-manager]
                block = 1
                while True:
                    data = await self._read_block(file)
                    if not await self._send_until_acked(sock, _data(block, data), block):
                        return
                    if len(data) < self.block_size:
                        self.logger.info(f"Sent '{self.filename}' to {self.client}")
                        return
                    block = (block + 1) % 65536

    async def _read_block(self, file) -> bytes:
        data = b""
        while len(data) < self.block_size and (chunk := await file.read(size=self.block_size - len(data))):
            data += chunk
        return data

    async def _send_until_acked(self, sock: ConnectedUDPSocket, packet: bytes, block: int) -> bool:
        """Send *packet* until the client acknowledges *block*.

        Returns False when the client reports an error or the retries run out. Any
        other packet is ignored: answering a duplicate ACK with a retransmission
        would duplicate every block that follows (RFC 1123, section 4.2.3.1).
        """
        for attempt in range(1, self.server.retries + 1):
            await sock.send(packet)
            with move_on_after(self.timeout):
                while True:
                    reply = await sock.receive()
                    if reply[:4] == _ack(block):
                        return True
                    if _opcode(reply) == Opcode.ERROR:
                        self.logger.warning(f"{self.client} ended the transfer: {_error_message(reply)}")
                        return False
            self.logger.warning(f"No ACK of block {block} from {self.client} (attempt {attempt}/{self.server.retries})")
        return False
