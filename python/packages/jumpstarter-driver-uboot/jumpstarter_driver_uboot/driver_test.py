import os
import platform
import shutil
import subprocess

import pytest
import requests
import rpmfile
from jumpstarter_driver_composite.driver import Composite, Proxy
from jumpstarter_driver_qemu.driver import Qemu

from .driver import UbootConsole
from jumpstarter.common.utils import serve

UBOOT_RPM_URL = "https://kojipkgs.fedoraproject.org/packages/uboot-tools/2025.10/1.fc43/noarch/uboot-images-armv8-2025.10-1.fc43.noarch.rpm"


@pytest.fixture(scope="session")
def uboot_image(tmpdir_factory):
    tmp_path = tmpdir_factory.mktemp("uboot-images")
    rpm_path = tmp_path / "uboot-images-armv8.rpm"
    bin_path = tmp_path / "u-boot.bin"

    print(f"\nDownloading u-boot RPM from {UBOOT_RPM_URL}")
    try:
        with requests.get(UBOOT_RPM_URL, stream=True, timeout=120) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            downloaded = 0
            with rpm_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
            print(f"Downloaded {downloaded} bytes (expected {total})")
    except requests.RequestException as e:
        raise AssertionError(f"Failed to download u-boot RPM: {e}") from e

    print("Extracting u-boot.bin from RPM...")
    if shutil.which("rpm2cpio") and shutil.which("cpio"):
        subprocess.run(
            f"rpm2cpio {rpm_path} | cpio -idm --quiet ./usr/share/uboot/qemu_arm64/u-boot.bin",
            shell=True, cwd=str(tmp_path), check=True,
        )
        extracted = tmp_path / "usr" / "share" / "uboot" / "qemu_arm64" / "u-boot.bin"
        extracted.rename(bin_path)
    else:
        with rpmfile.open(rpm_path) as rpm:
            fd = rpm.extractfile("./usr/share/uboot/qemu_arm64/u-boot.bin")
            with bin_path.open("wb") as f:
                f.write(fd.read())
    print(f"Extracted u-boot.bin ({bin_path.size()} bytes)")

    yield bin_path


@pytest.mark.xfail(
    platform.system() == "Darwin" and os.getenv("GITHUB_ACTIONS") == "true",
    reason="QEMU-based U-Boot tests are flaky on macOS in GitHub CI",
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
        root.qemu.flasher.flash(uboot_image, target="bios")

        uboot = root.uboot

        with uboot.reboot_to_console(debug=True):
            assert uboot.run_command_checked("version") == [
                "U-Boot 2025.10 (Oct 13 2025 - 00:00:00 +0000)",
                "",
                "gcc (GCC) 15.2.1 20250924 (Red Hat 15.2.1-2)",
                "GNU ld version 2.45-1.fc43",
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
