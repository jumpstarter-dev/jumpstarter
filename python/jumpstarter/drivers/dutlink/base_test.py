import socket
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep

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
        client.power.off()
        sleep(1)
        client.power.on()
        sleep(1)
        client.power.off()

        client.storage.host()
        client.storage.dut()
        client.storage.off()

        client.storage.write("/dev/null")

        with TemporaryDirectory() as tempdir:
            socketpath = Path(tempdir) / "socket"

            listener = client.portal.call(anyio.create_unix_listener, socketpath)

            with client.console.portforward(listener):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.connect(str(socketpath))

                    expect = fdspawn(s)
                    expect.send("about\r\n")
                    expect.expect("Jumpstarter test-harness")
