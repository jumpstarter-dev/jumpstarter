import json
import os
import platform
import sys
import tarfile
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests
from anyio import fail_after

from jumpstarter_driver_qemu.driver import Qemu, QemuFlasher

from jumpstarter.common.oci import OciCredentials
from jumpstarter.common.utils import serve


@pytest.fixture
def anyio_backend():
    """Use only asyncio backend for anyio tests."""
    return "asyncio"


@pytest.fixture(scope="session")
def ovmf(tmpdir_factory):
    tmp_path = tmpdir_factory.mktemp("ovmf")

    # renovate: datasource=github-releases depName=rust-osdev/ovmf-prebuilt
    ver = "edk2-stable202605-r1"
    url = f"https://github.com/rust-osdev/ovmf-prebuilt/releases/download/{ver}/{ver}-bin.tar.xz"

    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with (tmp_path / "ovmf.tar.xz").open("wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    with tarfile.open(tmp_path / "ovmf.tar.xz") as tf:
        tf.extractall(tmp_path, filter="data")

    yield tmp_path / f"{ver}-bin"


# Get native architecture
def get_native_arch_config():
    native_arch = platform.machine()
    if native_arch == "x86_64":
        return "x86_64", "x64"
    elif native_arch == "aarch64":
        return "aarch64", "aarch64"
    else:
        pytest.skip(f"Unsupported architecture: {native_arch}")  # ty: ignore[too-many-positional-arguments]


@pytest.mark.xfail(
    platform.system() == "Darwin" and os.getenv("GITHUB_ACTIONS") == "true",
    reason="QEMU tests are flaky on macOS in GitHub CI",
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
                f"https://download.fedoraproject.org/pub/fedora/linux/releases/43/Cloud/{arch}/images/Fedora-Cloud-Base-Generic-43-1.6.{arch}.qcow2",
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

    with (
        patch("jumpstarter_driver_qemu.driver.run_process", side_effect=_mock_qemu_img_info(current)),
        pytest.raises(RuntimeError, match="Shrinking disk is not supported"),
    ):
        await driver.children["power"].on()


@pytest.mark.anyio
async def test_resize_insufficient_space_blocked(resize_test):
    """Resize beyond available host space should raise RuntimeError."""
    driver, current = resize_test("100G", 10)  # requested: 100G, current: 10G

    mock_usage = SimpleNamespace(free=5 * 1024**3)  # only 5G free

    with (
        patch("jumpstarter_driver_qemu.driver.run_process", side_effect=_mock_qemu_img_info(current)),
        patch("jumpstarter_driver_qemu.driver.shutil.disk_usage", return_value=mock_usage),
        pytest.raises(RuntimeError, match="Not enough disk space"),
    ):
        await driver.children["power"].on()


@pytest.mark.anyio
async def test_resize_succeeds(resize_test):
    """Resize should call qemu-img resize with correct size."""
    driver, current = resize_test("20G", 10)  # requested: 20G, current: 10G
    mock_usage = SimpleNamespace(free=50 * 1024**3)

    with (
        patch("jumpstarter_driver_qemu.driver.run_process", side_effect=_mock_qemu_img_info(current)) as mock_run,
        patch("jumpstarter_driver_qemu.driver.shutil.disk_usage", return_value=mock_usage),
        patch("jumpstarter_driver_qemu.driver.Popen", side_effect=RuntimeError("mock popen")),
        pytest.raises(RuntimeError, match="mock popen"),
    ):
        await driver.children["power"].on()

    # Find the resize call and verify size argument
    resize_calls = [c for c in mock_run.call_args_list if "resize" in c.args[0]]
    assert resize_calls, "qemu-img resize should be called"
    resize_cmd = resize_calls[0].args[0]  # ['qemu-img', 'resize', path, size]
    assert resize_cmd[-1] == str(20 * 1024**3)


def _qemu_img(virtual_size, *, failing=()):
    """A run_process double that, like anyio's, raises on failure unless check=False."""

    async def run_process(cmd, *, check=True, **kwargs):
        returncode = 1 if any(subcommand in cmd for subcommand in failing) else 0
        stdout = b"" if returncode else json.dumps({"format": "raw", "virtual-size": virtual_size}).encode()
        stderr = b"qemu-img failed" if returncode else b""
        if check and returncode:
            raise CalledProcessError(returncode, cmd, stdout, stderr)
        return CompletedProcess(cmd, returncode, stdout, stderr)

    return run_process


@pytest.mark.anyio
async def test_failed_resize_reports_the_qemu_img_error(resize_test):
    driver, current = resize_test("20G", 10)

    with (
        patch("jumpstarter_driver_qemu.driver.run_process", side_effect=_qemu_img(current, failing=("resize",))),
        patch("jumpstarter_driver_qemu.driver.shutil.disk_usage", return_value=SimpleNamespace(free=50 * 1024**3)),
        pytest.raises(RuntimeError, match="Failed to resize disk: qemu-img failed"),
    ):
        await driver.children["power"].on()


@pytest.mark.anyio
async def test_unreadable_image_info_falls_back_to_raw(cmdline_test):
    driver = cmdline_test()

    with (
        patch("jumpstarter_driver_qemu.driver.run_process", side_effect=_qemu_img(0, failing=("info",))),
        patch("jumpstarter_driver_qemu.driver.Popen", side_effect=RuntimeError("mock popen")) as mock_popen,
        pytest.raises(RuntimeError, match="mock popen"),
    ):
        await driver.children["power"].on()

    assert mock_popen.called


@pytest.fixture
def cmdline_test():
    """Create a Qemu driver with a root disk, cleanup after test."""
    driver = None

    def _create(**kwargs):
        nonlocal driver
        driver = Qemu(**kwargs)
        root = Path(driver._tmp_dir.name) / "root"
        root.write_bytes(b"")
        return driver

    yield _create

    if driver:
        driver._tmp_dir.cleanup()


async def _captured_cmdline(driver):
    """Power on `driver`, aborting at Popen, and return the cmdline it was given."""
    with (
        patch("jumpstarter_driver_qemu.driver.run_process", side_effect=_mock_qemu_img_info(0)),
        patch("jumpstarter_driver_qemu.driver.Popen", side_effect=RuntimeError("mock popen")) as mock_popen,
        pytest.raises(RuntimeError, match="mock popen"),
    ):
        await driver.children["power"].on()

    return mock_popen.call_args.args[0]


@pytest.mark.anyio
async def test_virtio_transport_defaults_to_pci(cmdline_test):
    """Default transport should keep the existing PCI-attached devices."""
    cmdline = await _captured_cmdline(cmdline_test())

    assert "virtio-net-pci,netdev=eth0" in cmdline
    assert "virtio-blk-pci,drive=rootfs,bootindex=1" in cmdline
    assert "virtio-blk-pci,drive=cidata" in cmdline


@pytest.mark.anyio
async def test_virtio_transport_mmio(cmdline_test):
    """mmio transport should switch network and disk devices, for firmware
    (e.g. u-boot booting an aboot image) that can't enumerate PCI early enough
    to see them."""
    # mmio images need >=4G (their DTB hardcodes it); see below.
    cmdline = await _captured_cmdline(cmdline_test(virtio_transport="mmio", mem="4G"))

    assert "virtio-net-device,netdev=eth0" in cmdline
    assert "virtio-blk-device,drive=rootfs,bootindex=1" in cmdline
    # No cidata disk in mmio mode: u-boot's aboot bootcmd probes a single virtio
    # device (devnum=0), and QEMU's reverse virtio-mmio ordering would otherwise
    # let the GPT-less cidata disk shadow the real boot disk. Matches air --aboot.
    assert not any("cidata" in str(arg) for arg in cmdline)
    assert "virtio-net-pci,netdev=eth0" not in cmdline
    assert "virtio-blk-pci,drive=rootfs,bootindex=1" not in cmdline


@pytest.mark.anyio
async def test_virtio_transport_mmio_requires_4g(cmdline_test):
    """mmio boots AIB aboot images whose DTB hardcodes a 4G memory layout, so
    too-little memory must fail loudly instead of silently failing to boot."""
    driver = cmdline_test(virtio_transport="mmio")  # default mem=512M

    with patch("jumpstarter_driver_qemu.driver.Popen") as mock_popen, pytest.raises(RuntimeError, match="4G"):
        await driver.children["power"].on()

    mock_popen.assert_not_called()


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


def test_disk_dir_uses_tmp_by_default():
    driver = Qemu()
    assert driver._disk_dir == driver._tmp_dir.name


def test_disk_dir_stays_with_shared_in_tests(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    driver = Qemu(launcher_socket=str(shared / "launcher.sock"))
    assert driver._disk_dir == str(shared)


def test_disk_dir_is_slash_disk_in_production_sidecar():
    driver = Qemu(launcher_socket="/shared/launcher.sock")
    assert driver._disk_dir == "/disk"


def test_cidata_uses_tmp_by_default():
    """Local mode keeps cloud-init vvfat content under a system temp dir."""
    driver = Qemu()
    cidata = driver.cidata()
    try:
        assert Path(cidata.name).exists()
        assert (Path(cidata.name) / "meta-data").is_file()
        assert (Path(cidata.name) / "user-data").is_file()
        assert not str(cidata.name).startswith("/shared")
    finally:
        cidata.cleanup()


def test_cidata_uses_shared_work_dir_with_launcher_socket(tmp_path):
    """Sidecar mode must place cidata on the shared volume so QEMU can read it."""
    shared = tmp_path / "shared"
    shared.mkdir()
    # Real _work_dir derives from the launcher socket parent directory.
    driver = Qemu(launcher_socket=str(shared / "launcher.sock"))
    cidata = driver.cidata()
    try:
        assert Path(cidata.name).is_relative_to(shared)
        assert (Path(cidata.name) / "meta-data").is_file()
        assert (Path(cidata.name) / "user-data").is_file()
    finally:
        cidata.cleanup()


# OCI Flash Tests


FAKE_FLS = """\
import json, os, sys, time
with open(os.environ["FAKE_FLS_RECORD"], "w") as record:
    json.dump(
        {
            "argv": sys.argv[1:],
            "pid": os.getpid(),
            "username": os.environ.get("FLS_REGISTRY_USERNAME"),
            "password": os.environ.get("FLS_REGISTRY_PASSWORD"),
        },
        record,
    )
for line in os.environ["FAKE_FLS_STDOUT"].splitlines():
    print(line, flush=True)
for line in os.environ["FAKE_FLS_STDERR"].splitlines():
    print(line, file=sys.stderr, flush=True)
time.sleep(float(os.environ["FAKE_FLS_SLEEP"]))
sys.exit(int(os.environ["FAKE_FLS_EXIT"]))
"""


@pytest.fixture
def fake_fls(tmp_path, monkeypatch):
    """Point the flasher at a fake fls that records how it was invoked."""
    fls = tmp_path / "fls"
    fls.write_text(f"#!{sys.executable}\n{FAKE_FLS}")
    fls.chmod(0o755)
    record = tmp_path / "fls.json"
    monkeypatch.setenv("FAKE_FLS_RECORD", str(record))
    get_fls_binary = MagicMock(return_value=str(fls))
    monkeypatch.setattr("jumpstarter_driver_qemu.driver.get_fls_binary", get_fls_binary)

    def configure(*, stdout="", stderr="", exit_code=0, sleep=0):
        monkeypatch.setenv("FAKE_FLS_STDOUT", stdout)
        monkeypatch.setenv("FAKE_FLS_STDERR", stderr)
        monkeypatch.setenv("FAKE_FLS_EXIT", str(exit_code))
        monkeypatch.setenv("FAKE_FLS_SLEEP", str(sleep))

    configure()
    return SimpleNamespace(
        path=str(fls),
        configure=configure,
        get_fls_binary=get_fls_binary,
        invocation=lambda: json.loads(record.read_text()),
    )


def _assert_gone(pid):
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def _collect_flash_oci(flasher, *args, **kwargs):
    """Collect all output from flash_oci async generator."""
    results = []
    async for chunk in flasher.flash_oci(*args, **kwargs):
        results.append(chunk)
    return results


@pytest.mark.anyio
async def test_flash_oci_success(fake_fls):
    """flash_oci should invoke fls from-url with the correct arguments."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    expected_target = str(Path(driver._tmp_dir.name) / "root")
    fake_fls.configure(stdout="Flashing complete")

    results = await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")

    assert results[-1] == ("", "", 0)
    assert fake_fls.invocation()["argv"][:3] == ["from-url", "oci://quay.io/org/image:tag", expected_target]


@pytest.mark.anyio
async def test_flash_oci_with_partition(fake_fls):
    """flash_oci should write to the correct partition path."""
    driver = Qemu()
    flasher = driver.children["flasher"]

    await _collect_flash_oci(flasher, "oci://quay.io/org/bios:v1", partition="bios")

    assert fake_fls.invocation()["argv"][2] == str(Path(driver._tmp_dir.name) / "bios")


@pytest.mark.anyio
async def test_flash_oci_with_credentials(fake_fls):
    """OCI credentials should be passed via env vars, not command args."""
    flasher = Qemu().children["flasher"]

    await _collect_flash_oci(
        flasher, "oci://quay.io/private/image:tag", oci_username="myuser", oci_password="mypass"
    )

    invocation = fake_fls.invocation()
    assert "myuser" not in invocation["argv"]
    assert "mypass" not in invocation["argv"]
    assert (invocation["username"], invocation["password"]) == ("myuser", "mypass")


@pytest.mark.anyio
async def test_flash_oci_no_credentials(fake_fls):
    """Without credentials, fls inherits the environment and sees no registry login."""
    flasher = Qemu().children["flasher"]
    env_clean = {k: v for k, v in os.environ.items() if k not in ("OCI_USERNAME", "OCI_PASSWORD")}

    with (
        patch.dict(os.environ, env_clean, clear=True),
        patch("jumpstarter.common.oci.read_auth_file_credentials", return_value=OciCredentials()),
    ):
        await _collect_flash_oci(flasher, "oci://quay.io/public/image:tag")

    invocation = fake_fls.invocation()
    assert (invocation["username"], invocation["password"]) == (None, None)


@pytest.mark.anyio
async def test_flash_oci_credentials_from_env(fake_fls):
    """flash_oci should read OCI_USERNAME/OCI_PASSWORD from env when not explicitly provided."""
    flasher = Qemu().children["flasher"]

    with patch.dict(os.environ, {"OCI_USERNAME": "envuser", "OCI_PASSWORD": "envpass"}):
        await _collect_flash_oci(flasher, "oci://quay.io/private/image:tag")

    invocation = fake_fls.invocation()
    assert (invocation["username"], invocation["password"]) == ("envuser", "envpass")


@pytest.mark.anyio
async def test_flash_oci_explicit_credentials_override_env(fake_fls):
    """Explicit credentials should take precedence over env vars."""
    flasher = Qemu().children["flasher"]

    with patch.dict(os.environ, {"OCI_USERNAME": "envuser", "OCI_PASSWORD": "envpass"}):
        await _collect_flash_oci(
            flasher,
            "oci://quay.io/private/image:tag",
            oci_username="explicit_user",
            oci_password="explicit_pass",
        )

    invocation = fake_fls.invocation()
    assert (invocation["username"], invocation["password"]) == ("explicit_user", "explicit_pass")


@pytest.mark.anyio
async def test_flash_oci_streams_output(fake_fls):
    """flash_oci should yield stdout and stderr lines as they arrive."""
    flasher = Qemu().children["flasher"]
    fake_fls.configure(stdout="downloading layer 1\ndownloading layer 2", stderr="progress: 50%\nprogress: 100%")

    results = await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")

    assert [r[0] for r in results if r[0]] == ["downloading layer 1\n", "downloading layer 2\n"]
    assert [r[1] for r in results if r[1]] == ["progress: 50%\n", "progress: 100%\n"]
    assert results[-1] == ("", "", 0)


@pytest.mark.anyio
async def test_flash_oci_rejects_non_oci_url():
    """URLs without oci:// prefix should be rejected."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    assert isinstance(flasher, QemuFlasher)

    with pytest.raises(ValueError, match="OCI URL must start with oci://"):
        async for _ in flasher.flash_oci("docker://image:tag"):
            pass

    with pytest.raises(ValueError, match="OCI URL must start with oci://"):
        async for _ in flasher.flash_oci("quay.io/org/image:tag"):
            pass


@pytest.mark.anyio
async def test_flash_oci_partial_credentials_rejected():
    """Providing only username or only password should be rejected."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    assert isinstance(flasher, QemuFlasher)

    with pytest.raises(ValueError, match="OCI authentication requires both"):
        async for _ in flasher.flash_oci("oci://image:tag", oci_username="user", oci_password=None):
            pass

    with pytest.raises(ValueError, match="OCI authentication requires both"):
        async for _ in flasher.flash_oci("oci://image:tag", oci_username=None, oci_password="pass"):
            pass


@pytest.mark.anyio
async def test_flash_oci_fls_failure(fake_fls):
    """Non-zero return code from fls should raise RuntimeError."""
    flasher = Qemu().children["flasher"]
    fake_fls.configure(exit_code=1)

    with pytest.raises(RuntimeError, match="fls flash failed"):
        await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")


@pytest.mark.anyio
async def test_flash_oci_fls_timeout(fake_fls):
    """Flash should kill fls and raise RuntimeError when the timeout is exceeded."""
    flasher = Qemu(flash_timeout=1).children["flasher"]
    fake_fls.configure(sleep=60)

    with fail_after(10), pytest.raises(RuntimeError, match="fls flash timed out"):
        await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")

    _assert_gone(fake_fls.invocation()["pid"])


@pytest.mark.anyio
async def test_flash_oci_process_cleanup_on_early_exit(fake_fls):
    """Abandoning the output early should stop fls."""
    flasher = Qemu().children["flasher"]
    fake_fls.configure(stdout="line1\nline2", sleep=60)
    gen = flasher._stream_subprocess([fake_fls.path, "from-url", "oci://img", "/tmp/root"], None)  # ty: ignore[unresolved-attribute]

    with fail_after(10):
        async for _ in gen:
            break
        await gen.aclose()

    _assert_gone(fake_fls.invocation()["pid"])


@pytest.mark.anyio
async def test_flash_oci_fls_not_found(fake_fls):
    """FileNotFoundError should raise RuntimeError with install hint."""
    flasher = Qemu().children["flasher"]
    fake_fls.get_fls_binary.return_value = "/nonexistent/fls"

    with pytest.raises(RuntimeError, match="fls command not found"):
        await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")


@pytest.mark.anyio
async def test_flash_oci_uses_fls_config(fake_fls):
    """flash_oci should pass fls config from parent Qemu driver."""
    flasher = Qemu(fls_version="0.2.0").children["flasher"]

    await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")

    fake_fls.get_fls_binary.assert_called_once_with(
        fls_version="0.2.0",
        fls_binary_url=None,
        allow_custom_binaries=False,
    )


@pytest.mark.anyio
async def test_flash_oci_invalid_partition():
    """Invalid partition names should raise ValueError."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    assert isinstance(flasher, QemuFlasher)

    with pytest.raises(ValueError, match="invalid partition name"):
        async for _ in flasher.flash_oci("oci://image:tag", partition="nonexistent"):
            pass


# OCI Client Integration Tests


def test_flash_oci_via_flasher_client(fake_fls):
    """flasher.flash('oci://...') should route through flash_oci on the driver."""
    fake_fls.configure(stdout="done")

    with serve(Qemu()) as qemu:
        qemu.flasher.flash("oci://quay.io/org/image:tag")

    assert fake_fls.invocation()["argv"][:2] == ["from-url", "oci://quay.io/org/image:tag"]


def test_flash_oci_convenience_method(fake_fls):
    """qemu.flash_oci() should delegate to flasher.flash()."""
    with serve(Qemu()) as qemu:
        qemu.flash_oci("oci://quay.io/org/image:tag", partition="bios")

    argv = fake_fls.invocation()["argv"]
    assert argv[:2] == ["from-url", "oci://quay.io/org/image:tag"]
    assert Path(argv[2]).name == "bios"


@pytest.mark.anyio
async def test_flash_routes_oci_to_flash_oci():
    """Driver-side flash() should detect oci:// URLs and route to flash_oci."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    assert isinstance(flasher, QemuFlasher)

    async def mock_generator(*args, **kwargs):
        yield "", "", 0

    mock = MagicMock(side_effect=mock_generator)

    with patch.object(flasher, "flash_oci", mock):
        await flasher.flash("oci://quay.io/org/image:tag", partition="root")

        mock.assert_called_once_with("oci://quay.io/org/image:tag", "root")
