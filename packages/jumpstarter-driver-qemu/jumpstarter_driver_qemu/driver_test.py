import sys
import tarfile

import pytest
import requests
from opendal import Operator

from jumpstarter_driver_qemu.driver import Qemu

from jumpstarter.common.utils import serve


@pytest.fixture(scope="session")
def ovmf(tmpdir_factory):
    tmp_path = tmpdir_factory.mktemp("ovmf")

    ver = "edk2-stable202408.01-r1"
    url = f"https://github.com/rust-osdev/ovmf-prebuilt/releases/download/{ver}/{ver}-bin.tar.xz"

    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with (tmp_path / "ovmf.tar.xz").open("wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    tarfile.open(tmp_path / "ovmf.tar.xz").extractall(tmp_path, filter="data")

    yield tmp_path / f"{ver}-bin"


@pytest.mark.parametrize("arch,ovmf_arch", [("x86_64", "x64"), ("aarch64", "aarch64")])
def test_driver_qemu(tmp_path, ovmf, arch, ovmf_arch):
    with serve(Qemu(arch=arch)) as qemu:
        hostname = qemu.hostname
        username = qemu.username
        password = qemu.password

        qemu.flasher.flash(
            f"pub/fedora/linux/releases/41/Cloud/{arch}/images/Fedora-Cloud-Base-Generic-41-1.4.{arch}.qcow2",
            operator=Operator("http", endpoint="https://download.fedoraproject.org"),
        )

        qemu.flasher.flash(
            ovmf / ovmf_arch / "code.fd",
            partition="OVMF_CODE.fd",
        )

        qemu.flasher.flash(
            ovmf / ovmf_arch / "vars.fd",
            partition="OVMF_VARS.fd",
        )

        qemu.power.on()

        with qemu.novnc() as _:
            pass

        with qemu.console.pexpect() as p:
            p.logfile = sys.stdout.buffer
            p.expect_exact(f"{hostname} login:", timeout=240)
            p.sendline(username)
            p.expect_exact("Password:")
            p.sendline(password)
            p.expect_exact(f"[{username}@{hostname} ~]$")
            p.sendline("sudo setenforce 0")
            p.expect_exact(f"[{username}@{hostname} ~]$")

        with qemu.shell() as s:
            assert s.run("uname -r").stdout.strip() == f"6.11.4-301.fc41.{arch}"

        qemu.power.off()
