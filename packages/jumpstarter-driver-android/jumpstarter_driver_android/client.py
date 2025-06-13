import errno
import os
import socket
import subprocess
import sys
from contextlib import contextmanager
from threading import Event
from typing import Generator

import adbutils
import click
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

from jumpstarter.client import DriverClient


class AndroidClient(CompositeClient):
    """Generic Android client for controlling Android devices/emulators."""

    pass


class AdbClientBase(DriverClient):
    """
    Base class for ADB clients. This class provides a context manager to
    create an ADB client and forward the ADB server address and port.
    """

    def _check_port_in_use(self, host: str, port: int) -> bool:
        # Check if port is already bound
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, port))
        except socket.error as e:
            if e.errno == errno.EADDRINUSE:
                return True
        finally:
            sock.close()
        return False

    @contextmanager
    def forward_adb(self, host: str, port: int) -> Generator[str, None, None]:
        """
        Port-forward remote ADB server to local host and port.
        If the port is already bound, yields the existing address instead.

        Args:
            host (str): The local host to forward to.
            port (int): The local port to forward to.

        Yields:
            str: The address of the forwarded ADB server.
        """
        with TcpPortforwardAdapter(
            client=self,
            local_host=host,
            local_port=port,
        ) as addr:
            yield addr

    @contextmanager
    def adb_client(self, host: str = "127.0.0.1", port: int = 5038) -> Generator[adbutils.AdbClient, None, None]:
        """
        Context manager to get an `adbutils.AdbClient`.

        Args:
            host (str): The local host to forward to.
            port (int): The local port to forward to.

        Yields:
            adbutils.AdbClient: The `adbutils.AdbClient` instance.
        """
        with self.forward_adb(host, port) as addr:
            client = adbutils.AdbClient(host=addr[0], port=int(addr[1]))
            yield client


class AdbClient(AdbClientBase):
    """ADB client for interacting with Android devices."""

    def cli(self):
        @click.command(context_settings={"ignore_unknown_options": True})
        @click.option("host", "-H", default="127.0.0.1", show_default=True, help="Local adb host to forward to.")
        @click.option("port", "-P", type=int, default=5038, show_default=True, help="Local adb port to forward to.")
        @click.option("-a", is_flag=True, hidden=True)
        @click.option("-d", is_flag=True, hidden=True)
        @click.option("-e", is_flag=True, hidden=True)
        @click.option("-L", hidden=True)
        @click.option("--one-device", hidden=True)
        @click.option(
            "--adb",
            default="adb",
            show_default=True,
            help="Path to the ADB executable",
        )
        @click.argument("args", nargs=-1)
        def adb(
            host: str,
            port: int,
            adb: str,
            a: bool,
            d: bool,
            e: bool,
            l: str,  # noqa: E741
            one_device: str,
            args: tuple[str, ...],
        ):
            """
            Run adb using a local adb binary against the remote adb server.

            This command is a wrapper around the adb command-line tool. It allows you to run regular adb commands
            with an automatically forwarded adb server running on your Jumpstarter exporter.

            When executing this command, the exporter adb daemon is forwarded to a local port. The
            adb server address and port are automatically set in the environment variables ANDROID_ADB_SERVER_ADDRESS
            and ANDROID_ADB_SERVER_PORT, respectively. This configures your local adb client to communicate with the
            remote adb server.

            Most command line arguments and commands are passed directly to the adb CLI. However, some
            arguments and commands are not supported by the Jumpstarter adb client. These options include:
            -a, -d, -e, -L, --one-device.

            The following adb commands are also not supported in remote adb environments: connect, disconnect,
            reconnect, nodaemon, pair

            When running start-server or kill-server, Jumpstarter will start or kill the adb server on the exporter.

            Use the forward-adb command to forward the adb server address and port to a local port manually.
            """
            # Throw exception for all unsupported arguments
            if any([a, d, e, l, one_device]):
                raise click.UsageError(
                    "ADB options -a, -d, -e, -L, and --one-device are not supported by the Jumpstarter ADB client"
                )
            # Check for unsupported server management commands
            unsupported_commands = [
                "connect",
                "disconnect",
                "reconnect",
                "nodaemon",
                "pair",
            ]
            for arg in args:
                if arg in unsupported_commands:
                    raise click.UsageError(f"The adb command '{arg}' is not supported by the Jumpstarter ADB client")

            if "start-server" in args:
                remote_port = int(self.call("start_server"))
                click.echo(f"Remote adb server started on remote port exporter:{remote_port}")
                return 0
            elif "kill-server" in args:
                remote_port = int(self.call("kill_server"))
                click.echo(f"Remote adb server killed on remote port exporter:{remote_port}")
                return 0
            elif "forward-adb" in args:
                # Port is available, proceed with forwarding
                with self.forward_adb(host, port) as addr:
                    click.echo(f"Remote adb server forwarded to {addr[0]}:{addr[1]}")
                    Event().wait()

            # Forward the ADB server address and port and call ADB executable with args
            with self.forward_adb(host, port) as addr:
                env = os.environ | {
                    "ANDROID_ADB_SERVER_ADDRESS": addr[0],
                    "ANDROID_ADB_SERVER_PORT": str(addr[1]),
                }
                cmd = [adb, *args]
                process = subprocess.Popen(cmd, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, env=env)
                return process.wait()

        return adb


class ScrcpyClient(AdbClientBase):
    """Scrcpy client for controlling Android devices remotely."""

    def cli(self):
        @click.command(context_settings={"ignore_unknown_options": True})
        @click.option("host", "-H", default="127.0.0.1", show_default=True, help="Local adb host to forward to.")
        @click.option("port", "-P", type=int, default=5038, show_default=True, help="Local adb port to forward to.")
        @click.option(
            "--scrcpy",
            default="scrcpy",
            show_default=True,
            help="Path to the scrcpy executable",
        )
        @click.argument("args", nargs=-1)
        def scrcpy(
            host: str,
            port: int,
            scrcpy: str,
            args: tuple[str, ...],
        ):
            """
            Run scrcpy using a local executable against the remote adb server.

            This command is a wrapper around the scrcpy command-line tool. It allows you to run scrcpy
            against a remote Android device through an ADB server tunneled via Jumpstarter.

            When executing this command, the adb server address and port are forwarded to the local scrcpy executable.
            The adb server socket path is set in the environment variable ADB_SERVER_SOCKET, allowing scrcpy to
            communicate with the remote adb server.

            Most command line arguments are passed directly to the scrcpy executable.
            """
            # Unsupported scrcpy arguments that depend on direct adb server management
            unsupported_args = [
                "--connect",
                "-c",
                "--serial",
                "-s",
                "--select-usb",
                "--select-tcpip",
            ]

            for arg in args:
                for unsupported in unsupported_args:
                    if arg.startswith(unsupported):
                        raise click.UsageError(
                            f"Scrcpy argument '{unsupported}' is not supported by the Jumpstarter scrcpy client"
                        )

            # Forward the ADB server address and port and call scrcpy executable with args
            with self.forward_adb(host, port) as addr:
                # Scrcpy uses ADB_SERVER_SOCKET environment variable
                socket_path = f"tcp:{addr[0]}:{addr[1]}"
                env = os.environ | {
                    "ADB_SERVER_SOCKET": socket_path,
                }
                cmd = [scrcpy, *args]
                process = subprocess.Popen(cmd, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, env=env)
                return process.wait()

        return scrcpy
