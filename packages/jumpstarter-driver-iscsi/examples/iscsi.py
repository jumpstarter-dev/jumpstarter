#!/usr/bin/env python

import asyncclick as click

from jumpstarter.common.utils import env


@click.command()
@click.option("--file-size", default=10, type=int, help="Size of test image in MB")
@click.option("--filename", default="test.img", help="Name of test image file")
@click.option("--lun-name", default="test_disk", help="Name for the LUN")
async def main(file_size, filename, lun_name):
    file_size = int(file_size)

    with env() as client:
        iscsi = client.iscsi
        click.secho("iSCSI Server Example", fg="green")

        click.secho(f"Creating test image file: {filename} ({file_size}MB)", fg="blue")
        with open(filename, "wb") as f:
            f.write(b"Hello from iSCSI!\n" * 1024)
            f.truncate(file_size * 1024 * 1024)  # Create file of specified size

        click.secho("Starting iSCSI server", fg="blue")
        iscsi.start()

        dst_path = f"{lun_name}.img"
        click.secho(f"Uploading file '{filename}' to '{dst_path}'", fg="blue")
        iscsi.storage.write_from_path(dst_path, filename)

        click.secho(f"Creating LUN '{lun_name}' with size {file_size}MB", fg="blue")
        iscsi.add_lun(lun_name, dst_path, size_mb=file_size)

        host = iscsi.get_host()
        port = iscsi.get_port()
        target_iqn = iscsi.get_target_iqn()

        click.secho(f"\niSCSI server running at {host}:{port}", fg="green")
        click.secho(f"Target IQN: {target_iqn}", fg="green")

        luns = iscsi.list_luns()
        click.secho("\nAvailable LUNs:", fg="yellow")
        for lun in luns:
            click.secho(f"  - {lun['name']} ({lun['size'] / (1024 * 1024):.1f}MB)", fg="white")

        click.secho("\nConnect with:", fg="yellow")
        click.secho(f"sudo iscsiadm -m discovery -t sendtargets -p {host}:{port}", fg="white")
        click.secho(f"sudo iscsiadm -m node -T {target_iqn} -p {host}:{port} -l", fg="white")

        click.pause("\nPress any key to stop the server...")

        click.secho(f"Removing LUN '{lun_name}'", fg="blue")
        iscsi.remove_lun(lun_name)

        click.secho("Stopping iSCSI server", fg="blue")
        iscsi.stop()
        click.secho("iSCSI server stopped", fg="green")


if __name__ == "__main__":
    main()
