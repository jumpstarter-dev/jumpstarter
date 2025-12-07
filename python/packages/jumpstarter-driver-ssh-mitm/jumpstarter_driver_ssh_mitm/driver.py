"""
SSH MITM Driver: Secure SSH proxy with server-side key storage.

This driver implements a Man-in-the-Middle SSH proxy where the private key
never leaves the exporter. It uses paramiko to:
1. Accept SSH connections from clients (via Jumpstarter stream)
2. Connect to the target DUT using stored credentials
3. Proxy traffic between client and DUT
"""

import io
import logging
import socket
import threading
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import paramiko
from anyio import get_cancelled_exc_class
from anyio.from_thread import BlockingPortal
from jumpstarter_driver_network.driver import TcpNetwork

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, exportstream
from jumpstarter.streams.common import create_memory_stream

logging.getLogger("paramiko").setLevel(logging.WARNING)


class SSHMITMError(Exception):
    """Base exception for SSH MITM driver errors."""


BUFFER_SIZE = 65536


class StreamSocket:
    """
    Adapter to bridge async Jumpstarter streams with paramiko's blocking sockets.

    Paramiko requires a socket-like interface. This class uses a socketpair
    and forwarding threads to connect async streams to paramiko's Transport.
    """

    def __init__(self, send_stream, recv_stream, portal: BlockingPortal):
        self.client_sock, self.server_sock = socket.socketpair()
        self.client_sock.setblocking(True)
        self.server_sock.setblocking(True)

        self.client_sock.settimeout(1.0)
        self.server_sock.settimeout(1.0)

        self.send_stream = send_stream
        self.recv_stream = recv_stream
        self.portal = portal
        self._running = True

        self._recv_thread = threading.Thread(target=self._forward_recv, daemon=True)
        self._send_thread = threading.Thread(target=self._forward_send, daemon=True)

    def start(self):
        """Start bidirectional forwarding threads."""
        self._recv_thread.start()
        self._send_thread.start()

    def _forward_recv(self):
        """Forward: Jumpstarter stream → socket (for paramiko to read)."""
        socket_logger = logging.getLogger("SSHMITM.StreamSocket")
        try:
            while self._running:
                try:
                    data = self.portal.call(self.recv_stream.receive)
                    if data:
                        socket_logger.debug("recv->sock %d bytes", len(data))
                        self.client_sock.sendall(data)
                    else:
                        break
                except (BrokenPipeError, OSError):
                    break
            self._running = False
        except Exception as exc:
            socket_logger.debug("recv loop stopped: %s", exc)

    def _forward_send(self):
        """Forward: socket → Jumpstarter stream (paramiko writes)."""
        socket_logger = logging.getLogger("SSHMITM.StreamSocket")
        try:
            while self._running:
                try:
                    data = self.client_sock.recv(BUFFER_SIZE)
                    if data:
                        socket_logger.debug("sock->send %d bytes", len(data))
                        self.portal.call(self.send_stream.send, data)
                    else:
                        break
                except socket.timeout:
                    # Allow loop to check _running and exit cleanly
                    continue
                except (BrokenPipeError, OSError):
                    break
            self._running = False
        except Exception as exc:
            socket_logger.debug("send loop stopped: %s", exc)

    def get_paramiko_socket(self):
        """Get the socket for paramiko Transport."""
        return self.server_sock

    def close(self):
        """Clean up sockets."""
        self._running = False
        # Close async streams to unblock portal calls
        with suppress(Exception):
            self.portal.call(self.recv_stream.aclose)
        with suppress(Exception):
            self.portal.call(self.send_stream.aclose)
        try:
            self.client_sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.server_sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.client_sock.close()
        except Exception:
            pass
        try:
            self.server_sock.close()
        except Exception:
            pass
        self._recv_thread.join(timeout=5)
        self._send_thread.join(timeout=5)
        if self._recv_thread.is_alive() or self._send_thread.is_alive():
            logging.getLogger("SSHMITM.StreamSocket").debug("StreamSocket threads did not shut down cleanly")


class MITMServerInterface(paramiko.ServerInterface):
    """
    Paramiko server interface that accepts all authentication.
    Since clients have already authenticated through Jumpstarter's lease
    system, we accept any SSH authentication method here.
    """

    def __init__(self, allowed_username: str = "", default_dut_username: str = ""):
        self.allowed_username = allowed_username
        self.default_dut_username = default_dut_username
        self.client_username: str | None = None  # Username from client connection
        self.event = threading.Event()
        self.exec_command: str | None = None
        self.pty_width: int | None = None
        self.pty_height: int | None = None
        self.pty_term: str = "xterm"

    def _check_username(self, username: str | None) -> bool:
        if self.allowed_username and username and username != self.allowed_username:
            return False
        return True

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_auth_password(self, username, password):
        if self._check_username(username):
            self.client_username = username
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        if self._check_username(username):
            self.client_username = username
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_none(self, username):
        if self._check_username(username):
            self.client_username = username
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "none,password,publickey"

    def check_channel_shell_request(self, channel):
        self.exec_command = None
        self.event.set()
        return True

    def check_channel_exec_request(self, channel, command):
        self.exec_command = command.decode() if isinstance(command, bytes) else command
        self.event.set()
        return True

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        self.pty_term = term or "xterm"
        self.pty_width = width
        self.pty_height = height
        return True


@dataclass(kw_only=True)
class SSHMITM(Driver):
    """
    SSH MITM proxy driver with server-side key storage.
    This driver acts as a network layer that provides authenticated SSH proxy
    connections. It is designed to be used as a child of SSHWrapper.
    """

    default_username: str = ""
    ssh_identity: str | None = None
    ssh_identity_file: str | None = None
    channel_timeout: float = 30.0
    default_pty_width: int = 80
    default_pty_height: int = 24

    _host_key: Optional[paramiko.RSAKey] = field(init=False, default=None)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if "tcp" not in self.children:
            raise ConfigurationError("'tcp' child is required via ref, or directly as a TcpNetwork driver instance")

        if self.ssh_identity and self.ssh_identity_file:
            raise ConfigurationError("Cannot specify both ssh_identity and ssh_identity_file")

        if not self.ssh_identity and not self.ssh_identity_file:
            raise ConfigurationError("Either ssh_identity or ssh_identity_file must be provided")

        # Generate ephemeral host key for MITM server
        self._host_key = paramiko.RSAKey.generate(2048)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_network.client.NetworkClient"

    def _get_ssh_identity(self) -> str | None:
        """Get SSH private key content (internal use only)."""
        if self.ssh_identity:
            return self.ssh_identity
        if self.ssh_identity_file:
            try:
                return Path(self.ssh_identity_file).expanduser().read_text()
            except Exception as e:
                raise ConfigurationError(f"Failed to read ssh_identity_file '{self.ssh_identity_file}': {e}") from None
        return None

    def _get_target_connection(self) -> tuple[str, int]:
        """Get DUT host and port from TCP child driver."""
        tcp_driver: TcpNetwork = self.children["tcp"]
        return tcp_driver.host, tcp_driver.port or 22

    def _load_private_key(self, key_data: str) -> paramiko.PKey:
        """Load private key, auto-detecting type (Ed25519, RSA, ECDSA, DSS)."""
        key_file = io.StringIO(key_data)

        key_classes = [
            paramiko.Ed25519Key,
            paramiko.RSAKey,
            paramiko.ECDSAKey,
            paramiko.DSSKey,
        ]

        for key_class in key_classes:
            try:
                key_file.seek(0)
                return key_class.from_private_key(key_file)
            except (paramiko.SSHException, ValueError):
                continue

        raise SSHMITMError("Unable to load SSH key - unsupported key type")

    def _create_dut_client(self, dut_username: str | None = None) -> paramiko.SSHClient:
        """Create paramiko SSH client connected to DUT using stored key.

        Args:
            dut_username: Username to use for DUT connection. If None, uses default_username or "root".
        """
        target_host, target_port = self._get_target_connection()

        ssh_identity = self._get_ssh_identity()
        if not ssh_identity:
            raise SSHMITMError("SSH identity not available")

        pkey = self._load_private_key(ssh_identity)

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Use provided username, or fall back to default_username, or "root"
        username = dut_username or self.default_username or "root"

        self.logger.debug("Connecting to DUT: %s@%s:%d", username, target_host, target_port)

        try:
            client.connect(
                hostname=target_host,
                port=target_port,
                username=username,
                pkey=pkey,
                look_for_keys=False,
                allow_agent=False,
                timeout=10,
            )
        except Exception as e:
            self.logger.error("Failed to connect to DUT %s@%s:%d: %s", username, target_host, target_port, e)
            raise

        return client

    def _proxy_channels(self, client_channel, dut_channel):
        """Bidirectional proxy between client and DUT SSH channels."""

        def forward(src, dst, name):
            try:
                while True:
                    data = src.recv(BUFFER_SIZE)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception as e:
                self.logger.debug("Channel %s ended: %s", name, e)
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        t1 = threading.Thread(target=forward, args=(client_channel, dut_channel, "client→dut"), daemon=True)
        t2 = threading.Thread(target=forward, args=(dut_channel, client_channel, "dut→client"), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    def _open_dut_channel(self, server: MITMServerInterface) -> tuple[paramiko.SSHClient, paramiko.Channel]:
        """Open appropriate DUT channel (shell or exec) based on client request."""
        dut_username = server.client_username or server.default_dut_username
        dut_client = self._create_dut_client(dut_username=dut_username)
        transport = dut_client.get_transport()
        if transport is None:
            dut_client.close()
            raise SSHMITMError("Failed to open SSH transport for DUT")

        channel = transport.open_session()

        if server.exec_command:
            self.logger.debug("Executing command on DUT via MITM: %s", server.exec_command)
            channel.exec_command(server.exec_command)
        else:
            width = server.pty_width or self.default_pty_width
            height = server.pty_height or self.default_pty_height
            channel.get_pty(term=server.pty_term, width=width, height=height)
            channel.invoke_shell()

        return dut_client, channel

    def _handle_session(self, transport: paramiko.Transport):  # noqa: C901
        """Handle incoming SSH session: accept client, connect to DUT, proxy."""
        server = MITMServerInterface(self.default_username, default_dut_username=self.default_username)

        try:
            transport.add_server_key(self._host_key)
            transport.start_server(server=server)
        except paramiko.SSHException as e:
            self.logger.error("SSH negotiation failed: %s", e)
            return

        client_channel = transport.accept(timeout=self.channel_timeout)
        if client_channel is None:
            self.logger.error("No channel opened by client")
            return

        if not server.event.wait(timeout=self.channel_timeout):
            self.logger.error("No exec/shell request received before timeout")
            client_channel.close()
            return

        dut_client: paramiko.SSHClient | None = None
        dut_channel: paramiko.Channel | None = None
        try:
            dut_client, dut_channel = self._open_dut_channel(server)
            self.logger.info(
                "MITM proxy established: client <-> DUT (mode=%s)",
                "exec" if server.exec_command else "shell",
            )
            self._proxy_channels(client_channel, dut_channel)

            if server.exec_command:
                try:
                    exit_status = dut_channel.recv_exit_status()
                    client_channel.send_exit_status(exit_status)
                except Exception:
                    pass
                finally:
                    client_channel.close()

        except Exception as e:
            self.logger.error("Failed to connect to DUT: %s", e)
            client_channel.close()
        finally:
            if dut_channel:
                try:
                    dut_channel.close()
                except Exception:
                    pass
            if dut_client:
                try:
                    dut_client.close()
                except Exception:
                    pass
            transport.close()

    @exportstream
    @asynccontextmanager
    async def connect(self):
        """
        Stream endpoint for SSH proxy connections.

        When a client connects to this stream, we launch a paramiko-based
        SSH server that proxies traffic to the DUT. From the client's
        perspective this behaves like a normal SSH server.

        This is used by SSHWrapper as the 'tcp' child - SSHWrapper spawns
        a local SSH binary that connects through this proxy.
        """
        cancelled_exc = get_cancelled_exc_class()
        client_stream, server_stream = create_memory_stream()

        async with BlockingPortal() as portal:
            bridge = StreamSocket(
                send_stream=server_stream,
                recv_stream=server_stream,
                portal=portal,
            )
            bridge.start()

            transport = paramiko.Transport(bridge.get_paramiko_socket())
            server_thread = threading.Thread(target=self._handle_session, args=(transport,), daemon=True)
            server_thread.start()

            try:
                yield client_stream
            except (cancelled_exc, Exception) as e:
                if isinstance(e, cancelled_exc):
                    self.logger.debug("SSH stream cancelled by client")
                else:
                    self.logger.debug("SSH stream ended: %s", type(e).__name__)
            finally:
                with suppress(Exception):
                    transport.close()
                bridge.close()
                server_thread.join(timeout=5)
