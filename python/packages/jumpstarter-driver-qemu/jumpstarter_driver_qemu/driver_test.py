import sys

from opendal import Operator

from jumpstarter_driver_qemu.driver import Qemu

from jumpstarter.common.utils import serve


def test_driver_qemu(tmp_path):
    with serve(Qemu()) as qemu:
        hostname = qemu.hostname
        username = qemu.username
        password = qemu.password

        qemu.flasher.flash(
            "pub/fedora/linux/releases/41/Cloud/x86_64/images/Fedora-Cloud-Base-Generic-41-1.4.x86_64.qcow2",
            operator=Operator("http", endpoint="https://download.fedoraproject.org"),
        )

        qemu.power.on()

        with qemu.console.pexpect() as p:
            p.logfile = sys.stdout.buffer
            p.expect_exact(f"{hostname} login:", timeout=60)
            p.sendline(username)
            p.expect_exact("Password:")
            p.sendline(password)
            p.expect_exact(f"[{username}@{hostname} ~]$")
            p.sendline("sudo setenforce 0")
            p.expect_exact(f"[{username}@{hostname} ~]$")

        with qemu.shell() as s:
            assert s.run("uname -r").stdout.strip() == "6.11.4-301.fc41.x86_64"

        with qemu.novnc() as _:
            pass

        qemu.power.off()
