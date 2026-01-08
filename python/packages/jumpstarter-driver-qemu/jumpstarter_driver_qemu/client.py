from contextlib import contextmanager

import click
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.adapters import FabricAdapter, NovncAdapter

from jumpstarter.client.decorators import driver_click_group


class QemuClient(CompositeClient):
    @property
    def hostname(self) -> str:
        return self.call("get_hostname")

    @property
    def username(self) -> str:
        return self.call("get_username")

    @property
    def password(self) -> str:
        return self.call("get_password")

    def set_disk_size(self, size: str) -> None:
        """Set the disk size for resizing before boot."""
        self.call("set_disk_size", size)

    def set_memory_size(self, size: str) -> None:
        """Set the memory size for next boot."""
        self.call("set_memory_size", size)

    @contextmanager
    def novnc(self):
        with NovncAdapter(client=self.vnc) as url:
            yield url

    @contextmanager
    def shell(self):
        with FabricAdapter(
            client=self.ssh,
            user=self.username,
            connect_kwargs={"password": self.password},
        ) as conn:
            yield conn

    def cli(self):
        @driver_click_group(self)
        def base():
            """QEMU virtual machine operations"""
            pass

        @base.group()
        def resize():
            """Resize QEMU resources"""
            pass

        @resize.command(name="disk")
        @click.argument("size")
        def resize_disk(size):
            """Resize the root disk (e.g., 20G). Run before power on."""
            self.set_disk_size(size)
            click.echo(f"Disk will be resized to {size} on next power on")

        @resize.command(name="memory")
        @click.argument("size")
        def resize_memory(size):
            """Set memory size (e.g., 2G, 4G). Takes effect on next boot."""
            self.set_memory_size(size)
            click.echo(f"Memory will be set to {size} on next power on")

        return base
