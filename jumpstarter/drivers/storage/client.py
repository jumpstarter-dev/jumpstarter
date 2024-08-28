import click
from opendal import Operator

from jumpstarter.client import DriverClient
from jumpstarter.client.adapters import OpendalAdapter


class StorageMuxClient(DriverClient):
    def host(self):
        return self.call("host")

    def dut(self):
        return self.call("dut")

    def off(self):
        return self.call("off")

    def write(self, handle):
        return self.call("write", handle)

    def write_file(self, operator: Operator, path: str):
        with OpendalAdapter(client=self, operator=operator, path=path) as handle:
            return self.call("write", handle)

    def write_local_file(self, filepath):
        with OpendalAdapter(client=self, operator=Operator("fs", root="/"), path=filepath) as handle:
            return self.call("write", handle)

    def cli(self):
        @click.group
        def base():
            """Generic storage mux"""
            pass

        @base.command()
        def host():
            """Connect storage to host"""
            self.host()

        @base.command()
        def dut():
            """Connect storage to dut"""
            self.dut()

        @base.command()
        def off():
            """Disconnect storage"""
            self.off()

        @base.command()
        @click.argument("file")
        def write_local_file(file):
            self.write_local_file(file)

        return base
