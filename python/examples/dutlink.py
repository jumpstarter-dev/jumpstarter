import sys

import click

from jumpstarter.common.utils import serve
from jumpstarter.drivers.dutlink.base import Dutlink
from threading import Thread

instance = Dutlink(
    name="dutlink",
    serial="c415a913",
    storage_device="/dev/disk/by-id/usb-SanDisk_Extreme_Pro_52A456790D93-0:0",
)

def monitor_power(client):
    for reading in client.power.read():
        click.secho(f"{reading}", fg="red")

with serve(instance) as client:
    click.secho("Connected to Dutlink", fg="red")
    Thread(target=monitor_power, args=[client]).start()
    with client.console.expect() as expect:
        print("enter console")
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
