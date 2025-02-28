import sys
from time import sleep

from opendal import Operator

from jumpstarter_driver_qemu.driver import Qemu

from jumpstarter.common.utils import serve


def test_driver_qemu(tmp_path):
    with serve(Qemu(root_dir=str(tmp_path))) as qemu:
        qemu.image = "alpine.qcow2"
        assert qemu.image == "alpine.qcow2"

        qemu.storage.write_from_path(
            qemu.image,
            "alpine/v3.21/releases/cloud/generic_alpine-3.21.2-x86_64-bios-tiny-r0.qcow2",
            Operator("http", endpoint="https://dl-cdn.alpinelinux.org"),
        )

        qemu.start()

        sleep(3)
        with qemu.console.pexpect() as p:
            p.logfile = sys.stdout.buffer
            p.expect_exact("localhost login:", timeout=60)

        qemu.stop()
