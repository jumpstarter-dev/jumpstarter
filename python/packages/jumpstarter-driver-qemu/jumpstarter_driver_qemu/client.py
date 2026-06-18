from __future__ import annotations

import sys
from contextlib import contextmanager

import click
from fabric import Connection
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.adapters import FabricAdapter, NovncAdapter

from jumpstarter.client import FlasherClient
from jumpstarter.client.flasher import PathBuf
from jumpstarter.streams.encoding import Compression


class QemuFlasherClient(FlasherClient):
    """Flasher client for QEMU with OCI support via fls."""

    def flash(
        self,
        path: PathBuf | dict[str, PathBuf],
        *,
        target: str | None = None,
        compression: Compression | None = None,
    ):
        if isinstance(path, str) and path.startswith("oci://"):
            returncode = 0
            for stdout, stderr, code in self.streamingcall("flash_oci", path, target):
                if stdout:
                    print(stdout, end="", flush=True)
                if stderr:
                    print(stderr, end="", file=sys.stderr, flush=True)
                if code is not None:
                    returncode = code
            return returncode

        return super().flash(path, target=target, compression=compression)


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

    def flash_oci(self, oci_url: str, partition: str | None = None):
        """Flash an OCI image to the specified partition using fls.

        Convenience method that delegates to self.flasher.flash().

        Args:
            oci_url: OCI image reference (must start with oci://)
            partition: Target partition name (default: root)
        """
        return self.flasher.flash(oci_url, target=partition)

    @contextmanager
    def novnc(self):
        with NovncAdapter(client=self.vnc) as url:
            yield url

    @contextmanager
    def shell(self):
        # If the driver has an 'ssh' hostfwd entry, fetch the actual host port
        # (resolving any port=0 assignment) and connect directly over TCP.
        # Otherwise fall back to tunnelling through the jumpstarter stream (vsock).
        try:
            port = int(self.call("get_hostfwd_port", "ssh"))
            with Connection(
                host="127.0.0.1",
                port=port,
                user=self.username,
                connect_kwargs={"password": self.password},
            ) as conn:
                yield conn
        except KeyError:
            with FabricAdapter(
                client=self.ssh,
                user=self.username,
                connect_kwargs={"password": self.password},
            ) as conn:
                yield conn

    def cli(self):
        # Get the base group from CompositeClient which includes all child commands
        base = super().cli()

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
