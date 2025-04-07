from contextlib import AbstractContextManager
from ipaddress import IPv6Address, ip_address
from threading import Event

import asyncclick as click

from .adapters import DbusAdapter, TcpPortforwardAdapter
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
            """Forward local TCP port to remote network connection"""

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
