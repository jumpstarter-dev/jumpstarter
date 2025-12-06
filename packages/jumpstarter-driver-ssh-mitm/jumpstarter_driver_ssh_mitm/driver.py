"""
SSH MITM Driver - Secure SSH proxy with server-side key storage.

This driver implements a Man-in-the-Middle SSH proxy where the private key
never leaves the exporter. It uses paramiko to:
1. Accept SSH connections from clients (via Jumpstarter stream)
2. Connect to the target DUT using stored credentials
3. Proxy traffic between client and DUT

Security: The SSH private key stays on the exporter. Clients authenticate
through Jumpstarter's lease system, then the exporter authenticates to
the DUT on their behalf.

Usage:
    j ssh_mitm <cmd>            # Execute command via gRPC
    j ssh_mitm shell            # Native SSH via port forwarding
    j ssh_mitm shell --repl     # Interactive gRPC REPL shell
    j ssh_mitm forward -p 2222  # Port forward for ssh/scp/rsync
"""

import io
import logging
import shlex
import socket
import threading
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Optional

import paramiko
from anyio import get_cancelled_exc_class
from anyio.from_thread import BlockingPortal
from jumpstarter_driver_network.driver import TcpNetwork
from jumpstarter_driver_ssh.driver import SSHWrapper

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export, exportstream
from jumpstarter.streams.common import create_memory_stream

# Suppress noisy paramiko logs
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
        # Both sides blocking, run in dedicated threads
        self.client_sock.setblocking(True)
        self.server_sock.setblocking(True)

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
                except (BrokenPipeError, OSError):
                    break
        except Exception as exc:
            socket_logger.debug("send loop stopped: %s", exc)

    def get_paramiko_socket(self):
        """Get the socket for paramiko Transport."""
        return self.server_sock

    def close(self):
        """Clean up sockets."""
        self._running = False
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
            logging.getLogger("SSHMITM.StreamSocket").warning("StreamSocket threads did not shut down cleanly")


class MITMServerInterface(paramiko.ServerInterface):
    """
    Paramiko server interface that accepts all authentication.

    Since clients have already authenticated through Jumpstarter's lease
    system, we accept any SSH authentication method here.
    """

    def __init__(self, allowed_username: str = ""):
        self.allowed_username = allowed_username
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
        return paramiko.AUTH_SUCCESSFUL if self._check_username(username) else paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        return paramiko.AUTH_SUCCESSFUL if self._check_username(username) else paramiko.AUTH_FAILED

    def check_auth_none(self, username):
        return paramiko.AUTH_SUCCESSFUL if self._check_username(username) else paramiko.AUTH_FAILED

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

    Configuration:
        type: jumpstarter_driver_ssh_mitm.driver.SSHMITM
        ssh_identity_file: /path/to/key   # Or use ssh_identity for inline key
        default_username: root
        children:
            tcp:
                type: jumpstarter_driver_network.driver.TcpNetwork
                host: 192.168.1.100
                port: 22

    Client Commands:
        j ssh_mitm <cmd>            # Execute command via gRPC
        j ssh_mitm shell            # Native SSH via port forwarding
        j ssh_mitm shell --repl     # Interactive gRPC REPL shell
        j ssh_mitm forward -p 2222  # Port forward for ssh/scp/rsync

    Security:
        - Private key stored only on exporter (not exported via gRPC)
        - Clients authenticate via Jumpstarter lease system
    """

    default_username: str = ""
    ssh_identity: str | None = None
    ssh_identity_file: str | None = None
    channel_timeout: float = 30.0
    default_pty_width: int = 80
    default_pty_height: int = 24

    _host_key: Optional[paramiko.RSAKey] = field(init=False, default=None)
    _ssh_wrapper: Optional[SSHWrapper] = field(init=False, default=None)

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

        self._ssh_wrapper = SSHWrapper(
            default_username=self.default_username,
            ssh_command="ssh",
            ssh_identity=self.ssh_identity,
            ssh_identity_file=self.ssh_identity_file,
            children={"tcp": self.children["tcp"]},
        )

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_ssh_mitm.client.SSHMITMClient"

    @export
    def get_default_username(self) -> str:
        """Get default SSH username (exported for client display)."""
        return self._ssh_wrapper.get_default_username()

    # NOTE: Intentionally NOT exported - key must stay on server
    def get_ssh_identity(self) -> str | None:
        """Get SSH private key content (internal use only)."""
        return self._ssh_wrapper.get_ssh_identity()

    def _get_target_connection(self) -> tuple[str, int]:
        """Get DUT host and port from TCP child driver."""
        tcp_driver: TcpNetwork = self.children["tcp"]
        return tcp_driver.host, tcp_driver.port or 22

    def _load_private_key(self, key_data: str) -> paramiko.PKey:
        """
        Load private key, auto-detecting type (Ed25519, RSA, ECDSA, DSS).
        """
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

    def _create_dut_client(self) -> paramiko.SSHClient:
        """Create paramiko SSH client connected to DUT using stored key."""
        target_host, target_port = self._get_target_connection()

        ssh_identity = self.get_ssh_identity()
        if not ssh_identity:
            raise SSHMITMError("SSH identity not available")

        pkey = self._load_private_key(ssh_identity)

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        username = self.get_default_username() or "root"

        self.logger.debug("Connecting to DUT: %s@%s:%d", username, target_host, target_port)

        client.connect(
            hostname=target_host,
            port=target_port,
            username=username,
            pkey=pkey,
            look_for_keys=False,
            allow_agent=False,
        )

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
        dut_client = self._create_dut_client()
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

    def _handle_session(self, transport: paramiko.Transport):
        """Handle incoming SSH session: accept client, connect to DUT, proxy."""
        server = MITMServerInterface(self.get_default_username())

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

        server.event.wait(timeout=self.channel_timeout)

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
        Stream endpoint for interactive SSH sessions.

        When a client connects to this stream we launch a lightweight
        paramiko-based SSH server and bridge it to the Jumpstarter stream
        using StreamSocket. From the client's perspective this behaves like
        a normal SSH server, from the driver's perspective the traffic is
        proxied to the DUT.

        Note: When clients disconnect, you may see gRPC ExecuteBatchError
        tracebacks. This is a known framework limitation - the gRPC layer
        tries to send error status after the connection is already closed.
        The error is harmless and does not affect functionality.
        """
        cancelled_exc = get_cancelled_exc_class()
        client_stream, server_stream = create_memory_stream()

        async with BlockingPortal() as portal:
            # StreamSocket bridges bidirectional server_stream to paramiko's socket
            # Both send_stream and recv_stream use server_stream because:
            # - recv_stream: receives data FROM client (via client_stream) for paramiko to read
            # - send_stream: sends data TO client (via client_stream) from paramiko writes
            # The server_stream is bidirectional (StapledObjectStream) so it handles both directions
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
                # Suppress all exceptions during cleanup - connection may already be closed
                # This prevents the framework from trying to send error status on closed connections
                if isinstance(e, cancelled_exc):
                    self.logger.debug("SSH stream cancelled by client")
                else:
                    self.logger.debug("SSH stream ended: %s", type(e).__name__)
            finally:
                with suppress(Exception):
                    transport.close()
                bridge.close()
                server_thread.join(timeout=5)

    @export
    async def execute_command(self, *args) -> tuple[int, str, str]:
        """
        Execute command on DUT and return (exit_code, stdout, stderr).

        Used by 'j ssh_mitm <command>' for simple command execution.
        More efficient than shell for single commands.
        """
        dut_client: paramiko.SSHClient | None = None
        try:
            dut_client = self._create_dut_client()

            if not args:
                return (1, "", "No command provided")

            command = shlex.join(str(arg) for arg in args)
            self.logger.debug("Executing: %s", command)

            _, stdout, stderr = dut_client.exec_command(command)

            exit_code = stdout.channel.recv_exit_status()
            stdout_data = stdout.read().decode()
            stderr_data = stderr.read().decode()

            return (exit_code, stdout_data, stderr_data)

        except Exception as e:
            self.logger.error("Command execution failed: %s", e)
            return (1, "", str(e))
        finally:
            if dut_client:
                try:
                    dut_client.close()
                except Exception:
                    pass

    def close(self):
        """Clean up driver resources."""
        if self._ssh_wrapper:
            self._ssh_wrapper.close()
        super().close()
