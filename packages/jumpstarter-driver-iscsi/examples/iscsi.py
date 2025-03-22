#!/usr/bin/env python

import asyncclick as click
import os
from jumpstarter.common.utils import env
from jumpstarter_driver_opendal.client import fs_operator_for_path

@click.command()
@click.option("--image", required=True, help="Path to the bootable disk image to serve")
@click.option("--location", default=None, help="Where to store the image (file path or block device)")
@click.option("--lun-name", default="boot", help="Name for the LUN")
async def main(image, location, lun_name):
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

        # Determine if location is a block device
        is_block_device = False
        if location and location.startswith("/dev/"):
            is_block_device = True
            click.secho(f"Using block device: {location}", fg="blue")

            # Get the proper path and operator using the existing helper
            device_path, fs_operator = fs_operator_for_path(location)

            click.secho(f"Writing image to block device...", fg="blue")
            storage.write_from_path(str(device_path), image, operator=fs_operator)

            # Use the resolved path for the LUN
            target_path = str(device_path)
        else:
            # Regular file path within storage
            if location:
                target_path = location
            else:
                target_path = f"{lun_name}.img"

            click.secho(f"Using storage path: {target_path}", fg="blue")
            with open(image, "rb") as f:
                data = f.read()
                storage.write_bytes(target_path, data)

        # Create the LUN
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
        qemu_cmd = (
            f"qemu-system-x86_64 -m 2048 "
            f"-drive file=iscsi://{host}:{port}/{target_iqn}/0,format=raw "
        )
        click.secho(qemu_cmd, fg="white")

        click.pause("\nPress any key to stop the server...")

        click.secho(f"Removing LUN '{lun_name}'", fg="blue")
        iscsi.remove_lun(lun_name)

        click.secho("Stopping iSCSI server", fg="blue")
        iscsi.stop()
        click.secho("iSCSI server stopped", fg="green")


if __name__ == "__main__":
    main()
