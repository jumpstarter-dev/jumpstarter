import json
import os
import platform
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import requests
from opendal import Operator

from jumpstarter_driver_qemu.driver import Qemu

from jumpstarter.common.utils import serve


@pytest.fixture
def anyio_backend():
    """Use only asyncio backend for anyio tests."""
    return "asyncio"


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


# Get native architecture
def get_native_arch_config():
    native_arch = platform.machine()
    if native_arch == "x86_64":
        return "x86_64", "x64"
    elif native_arch == "aarch64":
        return "aarch64", "aarch64"
    else:
        pytest.skip(f"Unsupported architecture: {native_arch}") # ty: ignore[call-non-callable]


@pytest.mark.xfail(
    platform.system() == "Darwin" and os.getenv("GITHUB_ACTIONS") == "true",
    reason="QEMU tests are flaky on macOS in GitHub CI"
)
def test_driver_qemu(tmp_path, ovmf):
    arch, ovmf_arch = get_native_arch_config()

    with serve(
        Qemu(
            arch=arch,
            default_partitions={
                "OVMF_CODE.fd": ovmf / ovmf_arch / "code.fd",
                "OVMF_VARS.fd": ovmf / ovmf_arch / "vars.fd",
            },
        )
    ) as qemu:
        hostname = qemu.hostname
        username = qemu.username
        password = qemu.password

        cached_image = Path(__file__).parent.parent / "images" / f"Fedora-Cloud-Base-Generic-43-1.6.{arch}.qcow2"

        if cached_image.exists():
            qemu.flasher.flash(cached_image.resolve())
        else:
            qemu.flasher.flash(
                f"pub/fedora/linux/releases/43/Cloud/{arch}/images/Fedora-Cloud-Base-Generic-43-1.6.{arch}.qcow2",
                operator=Operator("http", endpoint="https://download.fedoraproject.org"),
            )

        qemu.power.on()

        with qemu.novnc() as _:
            pass

        with qemu.console.pexpect() as p:
            p.logfile = sys.stdout.buffer
            p.expect_exact(f"{hostname} login:", timeout=600)
            p.sendline(username)
            p.expect_exact("Password:")
            p.sendline(password)
            p.expect_exact(f"[{username}@{hostname} ~]$")
            p.sendline("sudo setenforce 0")
            p.expect_exact(f"[{username}@{hostname} ~]$")

        with qemu.shell() as s:
            assert s.run("uname -r").stdout.strip() == f"6.17.1-300.fc43.{arch}"

        qemu.power.off()


@pytest.fixture
def resize_test():
    """Create a Qemu driver with a sparse root disk, cleanup after test."""
    driver = None

    def _create(disk_size, current_size_gb):
        nonlocal driver
        driver = Qemu(disk_size=disk_size)
        root = Path(driver._tmp_dir.name) / "root"
        root.write_bytes(b"")
        os.truncate(root, current_size_gb * 1024**3)
        return driver, current_size_gb * 1024**3

    yield _create

    if driver:
        driver._tmp_dir.cleanup()


def _mock_qemu_img_info(virtual_size):
    """Return a mock for run_process that simulates qemu-img info."""
    async def mock(cmd, **kwargs):
        result = AsyncMock()
        result.returncode = 0
        result.stdout = json.dumps({"format": "raw", "virtual-size": virtual_size}).encode()
        result.check_returncode = lambda: None
        return result
    return mock


@pytest.mark.anyio
async def test_resize_shrink_blocked(resize_test):
    """Shrinking disk should raise RuntimeError."""
    driver, current = resize_test("10G", 20)  # requested: 10G, current: 20G

    with patch("jumpstarter_driver_qemu.driver.run_process", side_effect=_mock_qemu_img_info(current)):
        with pytest.raises(RuntimeError, match="Shrinking disk is not supported"):
            await driver.children["power"].on()


@pytest.mark.anyio
async def test_resize_insufficient_space_blocked(resize_test):
    """Resize beyond available host space should raise RuntimeError."""
    driver, current = resize_test("100G", 10)  # requested: 100G, current: 10G

    mock_usage = SimpleNamespace(free=5 * 1024**3)  # only 5G free

    with patch("jumpstarter_driver_qemu.driver.run_process", side_effect=_mock_qemu_img_info(current)):
        with patch("jumpstarter_driver_qemu.driver.shutil.disk_usage", return_value=mock_usage):
            with pytest.raises(RuntimeError, match="Not enough disk space"):
                await driver.children["power"].on()


@pytest.mark.anyio
async def test_resize_succeeds(resize_test):
    """Resize should call qemu-img resize with correct size."""
    driver, current = resize_test("20G", 10)  # requested: 20G, current: 10G
    mock_usage = SimpleNamespace(free=50 * 1024**3)

    with patch("jumpstarter_driver_qemu.driver.run_process", side_effect=_mock_qemu_img_info(current)) as mock_run:
        with patch("jumpstarter_driver_qemu.driver.shutil.disk_usage", return_value=mock_usage):
            # Mock Popen to stop before actually starting QEMU VM
            with patch("jumpstarter_driver_qemu.driver.Popen", side_effect=RuntimeError("mock popen")):
                with pytest.raises(RuntimeError, match="mock popen"):
                    await driver.children["power"].on()

    # Find the resize call and verify size argument
    resize_calls = [c for c in mock_run.call_args_list if "resize" in c.args[0]]
    assert resize_calls, "qemu-img resize should be called"
    resize_cmd = resize_calls[0].args[0]  # ['qemu-img', 'resize', path, size]
    assert resize_cmd[-1] == str(20 * 1024**3)


def test_set_disk_size_valid():
    """Valid size strings should be accepted."""
    driver = Qemu()
    driver.set_disk_size("20G")
    assert driver.disk_size == "20G"


def test_set_disk_size_invalid():
    """Invalid size strings should raise ValueError."""
    driver = Qemu()
    with pytest.raises(ValueError, match="Invalid size"):
        driver.set_disk_size("invalid")


def test_set_memory_size_valid():
    """Valid size strings should be accepted."""
    driver = Qemu()
    driver.set_memory_size("2G")
    assert driver.mem == "2G"


def test_set_memory_size_invalid():
    """Invalid size strings should raise ValueError."""
    driver = Qemu()
    with pytest.raises(ValueError, match="Invalid size"):
        driver.set_memory_size("invalid")
