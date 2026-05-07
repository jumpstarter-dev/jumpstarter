import os
import sys
from contextlib import contextmanager

import click
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.adapters import FabricAdapter, NovncAdapter
from jumpstarter_driver_opendal.client import FlasherClient


def _resolve_oci_password() -> str | None:
    """Read OCI password from environment, with OCI_PASSWORD_FILE fallback."""
    password = os.environ.get("OCI_PASSWORD")
    if password:
        return password
    password_file = os.environ.get("OCI_PASSWORD_FILE")
    if password_file:
        try:
            with open(password_file) as f:
                return f.read().strip()
        except OSError:
            pass
    return None


class QemuFlasherClient(FlasherClient):
    """Flasher client for QEMU with OCI support via fls."""

    def flash(self, path, *, target=None, operator=None, compression=None):
        if isinstance(path, str) and path.startswith("oci://"):
            oci_username = os.environ.get("OCI_USERNAME")
            oci_password = _resolve_oci_password()

            returncode = 0
            for stdout, stderr, code in self.streamingcall(
                "flash_oci", path, target, oci_username, oci_password
            ):
                if stdout:
                    print(stdout, end="", flush=True)
                if stderr:
                    print(stderr, end="", file=sys.stderr, flush=True)
                if code is not None:
                    returncode = code
            return returncode

        return super().flash(path, target=target, operator=operator, compression=compression)


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
