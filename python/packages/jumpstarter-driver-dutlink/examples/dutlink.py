#!/usr/bin/env python
import sys
import time

import click
from jumpstarter_driver_network.adapters import PexpectAdapter

from jumpstarter.common.utils import env

# initialize client from exporter config
# from jumpstarter.config.client import ClientConfigV1Alpha1
# with ClientConfigV1Alpha1.load("default").lease(selector="example.com/board=dutlink") as lease:
#     with lease.connect() as client:

# initialize client from environment
# e.g. `jmp-exporter shell dutlink`
if __name__ == "__main__":
    with env() as client:
        dutlink = client.dutlink
        click.secho("Connected to Dutlink", fg="green")
        # apply adapter to console for expect support
        with PexpectAdapter(client=dutlink.console) as console:
            # stream console output to stdout
            console.logfile = sys.stdout.buffer
            # ensure DUT is powered off
            dutlink.power.off()

            click.secho("Writing system image", fg="red")
            dutlink.storage.write_local_file("/tmp/nixos-visionfive2.img")
            click.secho("Written system image", fg="red")

            dutlink.storage.dut()
            click.secho("Connected storage device to DUT", fg="green")

            dutlink.power.on()
            click.secho("Powered DUT on", fg="green")

            click.secho("Waiting for boot menu", fg="red")
            console.expect("Enter choice:")
            console.sendline("1")
            click.secho("Selected boot entry", fg="red")

            click.secho("Waiting for login prompt", fg="red")
            console.expect("nixos@nixos", timeout=300)
            time.sleep(3)

            reading = next(dutlink.power.read())
            click.secho(f"Current power reading: {reading}", fg="blue")

            console.sendline("uname -a")
            console.expect("riscv64 GNU/Linux")

            dutlink.power.off()
