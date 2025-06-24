import os
import platform

import pytest
import requests
import rpmfile
from jumpstarter_driver_composite.driver import Composite, Proxy
from jumpstarter_driver_qemu.driver import Qemu

from .driver import UbootConsole
from jumpstarter.common.utils import serve


@pytest.fixture(scope="session")
def uboot_image(tmpdir_factory):
    tmp_path = tmpdir_factory.mktemp("uboot-images")

    url = "https://kojipkgs.fedoraproject.org/packages/uboot-tools/2024.10/1.fc41/noarch/uboot-images-armv8-2024.10-1.fc41.noarch.rpm"

    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with (tmp_path / "uboot-images-armv8.rpm").open("wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    with rpmfile.open(tmp_path / "uboot-images-armv8.rpm") as rpm:
        fd = rpm.extractfile("./usr/share/uboot/qemu_arm64/u-boot.bin")
        with (tmp_path / "u-boot.bin").open("wb") as f:
            f.write(fd.read())

    yield tmp_path / "u-boot.bin"


@pytest.mark.xfail(
    platform.system() == "Darwin" and os.getenv("GITHUB_ACTIONS") == "true",
    reason="QEMU-based U-Boot tests are flaky on macOS in GitHub CI"
)
def test_driver_uboot_console(uboot_image):
    print(uboot_image)
    with serve(
        Composite(
            children={
                "uboot": UbootConsole(
                    children={
                        "power": Proxy(ref="qemu.power"),
                        "serial": Proxy(ref="qemu.console"),
                    }
                ),
                "qemu": Qemu(arch="aarch64"),
            }
        )
    ) as root:
        root.qemu.flasher.flash(uboot_image, partition="bios")

        uboot = root.uboot

        with uboot.reboot_to_console(debug=True):
            assert uboot.run_command_checked("version") == [
                "U-Boot 2024.10 (Oct 11 2024 - 00:00:00 +0000)",
                "",
            ]

            print(uboot.setup_dhcp())

            uboot.set_env_dict(
                {
                    "foo": "bar",
                    "baz": "qux",
                }
            )

            assert uboot.get_env("foo") == "bar"
            assert uboot.get_env("baz") == "qux"

            uboot.set_env_dict(
                {
                    "foo": "qux",
                    "baz": None,
                }
            )

            assert uboot.get_env("foo") == "qux"
            assert uboot.get_env("baz") is None

            root.qemu.power.off()
