import sys
from threading import Thread

import click
from jumpstarter_driver_dutlink.driver import Dutlink

from jumpstarter.client.adapters import PexpectAdapter
from jumpstarter.common.utils import serve

instance = Dutlink(
    serial="c415a913",
    storage_device="/dev/disk/by-id/usb-SanDisk_Extreme_Pro_52A456790D93-0:0",
)


def monitor_power(client):
    try:
        for reading in client.power.read():
            click.secho(f"{reading}", fg="red")
    except Exception:
        pass


with serve(instance) as client:
    click.secho("Connected to Dutlink", fg="red")
    Thread(target=monitor_power, args=[client]).start()
    with PexpectAdapter(client=client.console) as expect:
        expect.logfile = sys.stdout.buffer

        expect.send("\x02" * 5)

        click.secho("Entering DUT console", fg="red")
        expect.send("console\r\n")
        expect.expect("Entering console mode")

        client.power.off()

        click.secho("Writing system image", fg="red")
        client.storage.write_local_file("/tmp/sdcard.img")
        click.secho("Written system image", fg="red")

        client.storage.dut()

        click.secho("Powering on DUT", fg="red")
        client.power.on()

        expect.expect("StarFive #")
        click.secho("Working around u-boot usb initialization issue", fg="red")
        expect.sendline("usb reset")

        expect.expect("StarFive #")
        expect.sendline("boot")

        expect.expect("Enter choice:")
        click.secho("Selecting boot entry", fg="red")
        expect.sendline("1")

        expect.expect("NixOS Stage 1")

        click.secho("Reached initrd", fg="red")

        expect.send("\x02" * 5)
        expect.expect("Exiting console mode")

        client.power.off()
