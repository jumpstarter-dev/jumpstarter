#!/usr/bin/env python

import os

import click
from jumpstarter_driver_opendal.client import operator_for_path

from jumpstarter.common.utils import env


def determine_architecture(arch, image):
    """Determine target architecture from parameter or auto-detect"""
    if arch != "auto":
        return arch

    import platform

    if "aarch64" in image.lower() or "arm64" in image.lower():
        return "aarch64"
    if "x86_64" in image.lower() or "amd64" in image.lower():
        return "x86_64"

    system_arch = platform.machine()
    return "aarch64" if system_arch in ["aarch64", "arm64"] else "x86_64"


def handle_file_storage(image, location, lun_name, storage):
    """Handle file storage setup and return target path and block device flag"""
    is_block_device = False

    if location and location.startswith("/dev/"):
        is_block_device = True
        click.secho(f"Using block device: {location}", fg="blue")
        if not click.confirm(
            f"Are you sure you want to write to block device {location}? This will overwrite all data!",
            default=False,
        ):
            raise click.Abort()

        device_path, fs_operator, _ = operator_for_path(location)
        click.secho("Writing image to block device...", fg="blue")
        storage.write_from_path(str(device_path), image, operator=fs_operator)
        target_path = str(device_path)
    else:
        target_path = location if location else f"{lun_name}.img"
        click.secho(f"Using storage path: {target_path}", fg="blue")
        storage.write_from_path(target_path, image)

    return target_path, is_block_device


def generate_qemu_command(target_arch, host, port, target_iqn):
    """Generate QEMU command based on architecture"""
    if target_arch == "aarch64":
        return f"qemu-system-aarch64 -m 2048 -machine virt -cpu cortex-a72 -drive file=iscsi://{host}:{port}/{target_iqn}/0,format=raw"

    return f"qemu-system-x86_64 -m 2048 -drive file=iscsi://{host}:{port}/{target_iqn}/0,format=raw"


@click.command()
@click.option("--image", required=True, help="Path to the bootable disk image to serve")
@click.option("--location", default=None, help="Where to store the image (file path or block device)")
@click.option("--lun-name", default="boot", help="Name for the LUN")
@click.option(
    "--arch",
    type=click.Choice(["x86_64", "aarch64", "auto"], case_sensitive=False),
    default="auto",
    help="Target architecture (auto-detect if not specified)",
)
def main(image, location, lun_name, arch):
    if not os.path.exists(image):
        click.secho(f"Error: Image '{image}' not found!", fg="red")
        return

    file_size_bytes = os.path.getsize(image)
    file_size_mb = file_size_bytes // (1024 * 1024)
    if file_size_mb == 0 and file_size_bytes > 0:
        file_size_mb = 1

    with env() as client:
        iscsi = client.iscsi
        storage = iscsi.storage

        click.secho("iSCSI Bootable Disk Server", fg="green")
        click.secho(f"Using image: {image} ({file_size_mb}MB)", fg="blue")

        click.secho("Starting iSCSI server", fg="blue")
        iscsi.start()

        target_path, is_block_device = handle_file_storage(image, location, lun_name, storage)

        click.secho(f"Creating LUN '{lun_name}' with size {file_size_mb}MB", fg="blue")
        iscsi.add_lun(lun_name, target_path, size_mb=file_size_mb, is_block=is_block_device)

        host = iscsi.get_host()
        port = iscsi.get_port()
        target_iqn = iscsi.get_target_iqn()

        click.secho(f"\niSCSI server running at {host}:{port}", fg="green")
        click.secho(f"Target IQN: {target_iqn}", fg="green")

        luns = iscsi.list_luns()
        click.secho("\nAvailable LUNs:", fg="yellow")
        for lun in luns:
            click.secho(f"  - {lun['name']} ({lun['size'] / (1024 * 1024):.1f}MB)", fg="white")

        click.secho("\nBoot with QEMU:", fg="yellow")
        target_arch = determine_architecture(arch, image)
        qemu_cmd = generate_qemu_command(target_arch, host, port, target_iqn)

        click.secho(f"Architecture: {target_arch}", fg="cyan")
        click.secho(qemu_cmd, fg="white")

        click.pause("\nPress any key to stop the server...")

        click.secho(f"Removing LUN '{lun_name}'", fg="blue")
        iscsi.remove_lun(lun_name)

        click.secho("Stopping iSCSI server", fg="blue")
        iscsi.stop()
        click.secho("iSCSI server stopped", fg="green")


if __name__ == "__main__":
    main()
