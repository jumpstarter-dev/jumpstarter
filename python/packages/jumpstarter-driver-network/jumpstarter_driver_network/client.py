from contextlib import AbstractContextManager
from ipaddress import IPv6Address, ip_address
from threading import Event

import click

from .adapters import DbusAdapter, TcpPortforwardAdapter, UnixPortforwardAdapter
from .driver import DbusNetwork
from jumpstarter.client import DriverClient


class NetworkClient(DriverClient):
    def cli(self):
        @click.group
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

        return base


class DbusNetworkClient(NetworkClient, AbstractContextManager):
    def __enter__(self):
        self.adapter = DbusAdapter(client=self)
        self.adapter.__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        self.adapter.__exit__(exc_type, exc_value, traceback)

    @property
    def kind(self):
        return self.labels[DbusNetwork.KIND_LABEL]
