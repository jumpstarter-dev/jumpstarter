from collections.abc import Generator
from contextlib import contextmanager
from ipaddress import IPv6Address, ip_address
from threading import Event
from typing import Any, Tuple
from urllib.parse import urlparse

import click
from anyio import ContextManagerMixin

from .adapters import DbusAdapter, TcpPortforwardAdapter, UnixPortforwardAdapter
from .driver import DbusNetwork
from jumpstarter.client import DriverClient
from jumpstarter.client.core import DriverMethodNotImplemented
from jumpstarter.client.decorators import driver_click_group


class NetworkClient(DriverClient):

    def address(self):
        return self.call("address")

    def cli(self):
        @driver_click_group(self)
        def base():
            """Generic Network Connection"""
            pass

        @base.command()
        @click.option("--address", default="localhost", show_default=True)
        @click.argument("port", type=int)
        def forward_tcp(address: str, port: int):
            """
            Forward local TCP port to remote network

            PORT is the TCP port to listen on.
            """

            with TcpPortforwardAdapter(
                client=self,
                local_host=address,
                local_port=port,
            ) as addr:
                host = ip_address(addr[0])
                port = addr[1]
                match host:
                    case IPv6Address():
                        click.echo("[{}]:{}".format(host, port))
                    case _:
                        click.echo("{}:{}".format(host, port))

                Event().wait()

        @base.command()
        @click.argument("path", type=click.Path(), required=False)
        def forward_unix(path: str | None):
            """
            Forward local Unix domain socket to remote network

            PATH is the path of the Unix domain socket to listen on,
            defaults to a random path under $XDG_RUNTIME_DIR.
            """

            with UnixPortforwardAdapter(
                client=self,
                path=path,
            ) as addr:
                click.echo(addr)

                Event().wait()

        @base.command()
        @click.option("--host", is_flag=True)
        @click.option("--port", is_flag=True)
        def address(host, port):
            """
            Direct TCP connection to remote network
            """
            try:
                addr = self.address()
                if not host and not port or host and port:
                    # Strip any URL scheme for clean display
                    clean_addr = _strip_scheme(addr)
                    click.echo(clean_addr)
                else:
                    # Parse address safely to handle IPv6
                    parsed_host, parsed_port = _parse_address(addr)
                    click.echo(parsed_host if host else parsed_port)
            except ValueError as e:
                raise click.ClickException(
                    f"enable_address mode is not true in the exporter configuration: {e}"
                ) from e
            except DriverMethodNotImplemented as e:
                raise click.ClickException(
                    "This exporter does not support direct connection yet, update exporter to 0.7.1 or later"
                ) from e


        return base

class DbusNetworkClient(NetworkClient, ContextManagerMixin):
    @contextmanager
    def __contextmanager__(self) -> Generator[Any]:
        with DbusAdapter(client=self) as value:
            yield value

    @property
    def kind(self):
        return self.labels[DbusNetwork.KIND_LABEL]


def _parse_address(addr: str) -> Tuple[str, str]:
    """Parse a host:port address string, handling IPv6 addresses correctly.

    Uses urllib.parse.urlparse for robust parsing of network addresses.

    Returns:
        Tuple of (host, port) as strings

    Examples:
        "127.0.0.1:8080" -> ("127.0.0.1", "8080")
        "[::1]:8080" -> ("::1", "8080")
        "localhost:8080" -> ("localhost", "8080")
    """
    # Add a dummy scheme to make it a valid URL for urlparse
    if not addr.startswith(("http://", "https://", "tcp://", "udp://")):
        addr = f"tcp://{addr}"

    parsed = urlparse(addr)
    host = parsed.hostname or ""
    port = str(parsed.port) if parsed.port else ""

    return host, port


def _strip_scheme(addr: str) -> str:
    """Remove URL scheme from address string for clean display.

    Uses urllib.parse.urlparse to properly handle various URL formats.

    Returns:
        Address string without scheme prefix

    Examples:
        "tcp://127.0.0.1:8080" -> "127.0.0.1:8080"
        "udp://[::1]:8080" -> "[::1]:8080"
        "127.0.0.1:8080" -> "127.0.0.1:8080"
    """
    # Handle IPv6 addresses in brackets specially
    if "://[" in addr and "]" in addr:
        # Find the scheme separator and the closing bracket
        scheme_end = addr.find("://")
        if scheme_end != -1:
            # Extract everything after "://"
            return addr[scheme_end + 3:]

    # For other cases, use urlparse
    parsed = urlparse(addr)
    # Reconstruct the address without scheme
    if parsed.port:
        return f"{parsed.hostname}:{parsed.port}"
    else:
        return parsed.hostname or addr
