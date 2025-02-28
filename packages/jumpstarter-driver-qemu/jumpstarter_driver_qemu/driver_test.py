import sys
from time import sleep

from opendal import Operator

from jumpstarter_driver_qemu.driver import Qemu

from jumpstarter.common.utils import serve


def test_driver_qemu(tmp_path):
    with serve(Qemu(root_dir=str(tmp_path))) as qemu:
        qemu.image = "fedora.qcow2"
        assert qemu.image == "fedora.qcow2"

        qemu.storage.write_from_path(
            qemu.image,
            "pub/fedora/linux/releases/41/Cloud/x86_64/images/Fedora-Cloud-Base-Generic-41-1.4.x86_64.qcow2",
            Operator("http", endpoint="https://download.fedoraproject.org"),
        )

        qemu.start()

        sleep(3)
        with qemu.console.pexpect() as p:
            p.logfile = sys.stdout.buffer
            p.expect_exact("cloudimg login:", timeout=60)
            p.sendline("jumpstarter")
            p.expect_exact("Password:")
            p.sendline("password")
            p.expect_exact("[jumpstarter@cloudimg ~]$")
            p.sendline("sudo setenforce 0")
            p.expect_exact("[jumpstarter@cloudimg ~]$")

        with qemu.shell() as s:
            assert s.run("uname -r").stdout.strip() == "6.11.4-301.fc41.x86_64"

        with qemu.novnc() as _:
            pass

        qemu.stop()
