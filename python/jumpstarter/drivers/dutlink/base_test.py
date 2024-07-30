import socket
from pathlib import Path
from tempfile import TemporaryDirectory

import anyio
from pexpect.fdpexpect import fdspawn

from jumpstarter.common.utils import serve
from jumpstarter.drivers.dutlink.base import Dutlink


def test_drivers_dutlink():
    with serve(
        Dutlink(
            name="dutlink",
            storage_device="/dev/null",
        )
    ) as client:
        with TemporaryDirectory() as tempdir:
            socketpath = Path(tempdir) / "socket"

            listener = client.portal.call(anyio.create_unix_listener, socketpath)

            with client.console.expect() as expect:
                expect.send("\x02" * 5)

                expect.send("about\r\n")
                expect.expect("Jumpstarter test-harness")

                expect.send("console\r\n")
                expect.expect("Entering console mode")

                client.power.off()

                client.storage.write("/dev/null")
                client.storage.dut()

                client.power.on()

                expect.send("\x02" * 5)
                expect.expect("Exiting console mode")

                client.power.off()
