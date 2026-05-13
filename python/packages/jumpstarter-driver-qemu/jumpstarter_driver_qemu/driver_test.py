import json
import os
import platform
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import anyio.abc
import pytest
import requests
from opendal import Operator

from jumpstarter_driver_qemu.driver import Qemu, QemuFlasher

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
        pytest.skip(f"Unsupported architecture: {native_arch}")  # ty: ignore[call-non-callable]


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


# OCI Flash Tests


def _create_mock_stream(lines):
    """Create a mock byte receive stream that yields lines then raises EndOfStream."""
    encoded = [line.encode() if isinstance(line, str) else line for line in lines]
    data = b"".join(encoded)

    stream = MagicMock(spec=anyio.abc.ByteReceiveStream)
    call_count = {"n": 0}

    async def mock_receive(max_bytes=65536):
        if call_count["n"] >= 1:
            raise anyio.EndOfStream()
        call_count["n"] += 1
        return data

    stream.receive = mock_receive
    stream.aclose = AsyncMock()
    return stream


def _create_mock_process(stdout_lines=None, stderr_lines=None, returncode=0):
    """Create a mock subprocess process for testing flash_oci."""
    if stdout_lines is None:
        stdout_lines = []
    if stderr_lines is None:
        stderr_lines = []

    process = MagicMock()
    process.returncode = returncode
    process.wait = AsyncMock(return_value=returncode)
    process.kill = MagicMock()

    process.stdout = _create_mock_stream(stdout_lines)
    process.stderr = _create_mock_stream(stderr_lines)

    return process


async def _collect_flash_oci(flasher, *args, **kwargs):
    """Collect all output from flash_oci async generator."""
    results = []
    async for chunk in flasher.flash_oci(*args, **kwargs):
        results.append(chunk)
    return results


@pytest.mark.anyio
async def test_flash_oci_success():
    """flash_oci should invoke fls from-url with the correct arguments."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    expected_target = str(Path(driver._tmp_dir.name) / "root")
    mock_process = _create_mock_process(stdout_lines=["Flashing complete\n"])

    with patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="/usr/local/bin/fls"):
        with patch(
            "jumpstarter_driver_qemu.driver.anyio.open_process", new_callable=AsyncMock, return_value=mock_process
        ) as mock_exec:
            results = await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")

            # Verify final chunk has returncode 0
            assert any(r[2] == 0 for r in results)

            mock_exec.assert_called_once()
            cmd = mock_exec.call_args.args[0]
            assert cmd[0] == "/usr/local/bin/fls"
            assert cmd[1] == "from-url"
            assert cmd[2] == "oci://quay.io/org/image:tag"
            assert cmd[3] == expected_target


@pytest.mark.anyio
async def test_flash_oci_with_partition():
    """flash_oci should write to the correct partition path."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    expected_target = str(Path(driver._tmp_dir.name) / "bios")
    mock_process = _create_mock_process()

    with patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"):
        with patch(
            "jumpstarter_driver_qemu.driver.anyio.open_process", new_callable=AsyncMock, return_value=mock_process
        ) as mock_exec:
            await _collect_flash_oci(flasher, "oci://quay.io/org/bios:v1", partition="bios")

            assert mock_exec.call_args.args[0][3] == expected_target


@pytest.mark.anyio
async def test_flash_oci_with_credentials():
    """OCI credentials should be passed via env vars, not command args."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process()

    with patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"):
        with patch(
            "jumpstarter_driver_qemu.driver.anyio.open_process", new_callable=AsyncMock, return_value=mock_process
        ) as mock_exec:
            await _collect_flash_oci(
                flasher,
                "oci://quay.io/private/image:tag",
                oci_username="myuser",
                oci_password="mypass",
            )

            # Credentials should NOT appear in command args
            assert "myuser" not in mock_exec.call_args.args
            assert "mypass" not in mock_exec.call_args.args

            # Credentials should be in env vars
            env = mock_exec.call_args.kwargs["env"]
            assert env["FLS_REGISTRY_USERNAME"] == "myuser"
            assert env["FLS_REGISTRY_PASSWORD"] == "mypass"


@pytest.mark.anyio
async def test_flash_oci_no_credentials():
    """Without credentials, env should be None (inherit parent env)."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process()

    # Ensure OCI env vars are not set so driver doesn't pick them up
    env_clean = {k: v for k, v in os.environ.items() if k not in ("OCI_USERNAME", "OCI_PASSWORD")}
    with patch.dict(os.environ, env_clean, clear=True):
        with patch("jumpstarter.common.oci.read_auth_file_credentials", return_value=(None, None)):
            with patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"):
                with patch(
                    "jumpstarter_driver_qemu.driver.anyio.open_process",
                    new_callable=AsyncMock,
                    return_value=mock_process,
                ) as mock_exec:
                    await _collect_flash_oci(flasher, "oci://quay.io/public/image:tag")

                    env = mock_exec.call_args.kwargs["env"]
                    assert env is None


@pytest.mark.anyio
async def test_flash_oci_credentials_from_env():
    """flash_oci should read OCI_USERNAME/OCI_PASSWORD from env when not explicitly provided."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process()

    with patch.dict(os.environ, {"OCI_USERNAME": "envuser", "OCI_PASSWORD": "envpass"}):
        with patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"):
            with patch(
                "jumpstarter_driver_qemu.driver.anyio.open_process", new_callable=AsyncMock, return_value=mock_process
            ) as mock_exec:
                await _collect_flash_oci(flasher, "oci://quay.io/private/image:tag")

                env = mock_exec.call_args.kwargs["env"]
                assert env["FLS_REGISTRY_USERNAME"] == "envuser"
                assert env["FLS_REGISTRY_PASSWORD"] == "envpass"


@pytest.mark.anyio
async def test_flash_oci_explicit_credentials_override_env():
    """Explicit credentials should take precedence over env vars."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process()

    with patch.dict(os.environ, {"OCI_USERNAME": "envuser", "OCI_PASSWORD": "envpass"}):
        with patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"):
            with patch(
                "jumpstarter_driver_qemu.driver.anyio.open_process", new_callable=AsyncMock, return_value=mock_process
            ) as mock_exec:
                await _collect_flash_oci(
                    flasher,
                    "oci://quay.io/private/image:tag",
                    oci_username="explicit_user",
                    oci_password="explicit_pass",
                )

                env = mock_exec.call_args.kwargs["env"]
                assert env["FLS_REGISTRY_USERNAME"] == "explicit_user"
                assert env["FLS_REGISTRY_PASSWORD"] == "explicit_pass"


@pytest.mark.anyio
async def test_flash_oci_streams_output():
    """flash_oci should yield stdout and stderr chunks as they arrive."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process(
        stdout_lines=["downloading layer 1\n", "downloading layer 2\n"],
        stderr_lines=["progress: 50%\n", "progress: 100%\n"],
    )

    with patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"):
        with patch(
            "jumpstarter_driver_qemu.driver.anyio.open_process", new_callable=AsyncMock, return_value=mock_process
        ):
            results = await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")

            # Should have received streaming output plus the final returncode chunk
            stdout_chunks = [r[0] for r in results if r[0]]
            stderr_chunks = [r[1] for r in results if r[1]]
            assert len(stdout_chunks) > 0
            assert len(stderr_chunks) > 0
            assert any(r[2] == 0 for r in results)


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
async def test_flash_oci_fls_failure():
    """Non-zero return code from fls should raise RuntimeError."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process(returncode=1)

    with patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"):
        with patch(
            "jumpstarter_driver_qemu.driver.anyio.open_process", new_callable=AsyncMock, return_value=mock_process
        ):
            with pytest.raises(RuntimeError, match="fls flash failed"):
                await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")


@pytest.mark.anyio
async def test_flash_oci_fls_timeout():
    """Flash should raise RuntimeError when timeout is exceeded."""
    driver = Qemu(flash_timeout=0)  # Immediate timeout
    flasher = driver.children["flasher"]

    async def hanging_receive(max_bytes=65536):
        await anyio.sleep(10)
        return b""

    mock_process = MagicMock()
    mock_process.returncode = None

    async def mock_wait():
        mock_process.returncode = -9
        return -9

    mock_process.wait = mock_wait
    mock_process.kill = MagicMock()

    stdout_stream = MagicMock(spec=anyio.abc.ByteReceiveStream)
    stdout_stream.receive = hanging_receive
    stdout_stream.aclose = AsyncMock()
    mock_process.stdout = stdout_stream

    stderr_stream = MagicMock(spec=anyio.abc.ByteReceiveStream)
    stderr_stream.receive = hanging_receive
    stderr_stream.aclose = AsyncMock()
    mock_process.stderr = stderr_stream

    with patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"):
        with patch(
            "jumpstarter_driver_qemu.driver.anyio.open_process", new_callable=AsyncMock, return_value=mock_process
        ):
            with pytest.raises(RuntimeError, match="fls flash timed out"):
                await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")

            mock_process.kill.assert_called_once()


@pytest.mark.anyio
async def test_flash_oci_inner_wait_timeout():
    """move_on_after timeout should continue the loop without raising."""
    driver = Qemu(flash_timeout=600)
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process(stdout_lines=["output\n"])

    with patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"):
        with patch(
            "jumpstarter_driver_qemu.driver.anyio.open_process", new_callable=AsyncMock, return_value=mock_process
        ):
            results = await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")

            final_results = [r for r in results if r[2] is not None]
            assert len(final_results) == 1, "exactly one final result with returncode expected"
            assert final_results[0][2] == 0
            stdout_chunks = [r[0] for r in results if r[0]]
            assert len(stdout_chunks) > 0, "output data should have been received"


@pytest.mark.anyio
async def test_flash_oci_process_cleanup_on_early_exit():
    """Finally block should kill process when generator is abandoned early."""
    driver = Qemu()
    flasher = driver.children["flasher"]

    mock_process = MagicMock()
    mock_process.returncode = None

    async def mock_wait():
        mock_process.returncode = 0
        return 0

    mock_process.wait = mock_wait
    mock_process.kill = MagicMock()

    mock_process.stdout = _create_mock_stream(["line1\n", "line2\n"])
    mock_process.stderr = _create_mock_stream([])

    with patch("jumpstarter_driver_qemu.driver.anyio.open_process", new_callable=AsyncMock, return_value=mock_process):
        gen = flasher._stream_subprocess(["fls", "from-url", "oci://img", "/tmp/root"], None)  # ty: ignore[unresolved-attribute]
        async for _ in gen:
            break
        # GeneratorExit inside an anyio task group is wrapped in a
        # BaseExceptionGroup; this is expected structured concurrency behavior.
        try:
            await gen.aclose()
        except BaseExceptionGroup:
            pass

        mock_process.kill.assert_called()


@pytest.mark.anyio
async def test_flash_oci_fls_not_found():
    """FileNotFoundError should raise RuntimeError with install hint."""
    driver = Qemu()
    flasher = driver.children["flasher"]

    with patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"):
        with patch(
            "jumpstarter_driver_qemu.driver.anyio.open_process", new_callable=AsyncMock, side_effect=FileNotFoundError
        ):
            with pytest.raises(RuntimeError, match="fls command not found"):
                await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")


@pytest.mark.anyio
async def test_flash_oci_uses_fls_config():
    """flash_oci should pass fls config from parent Qemu driver."""
    driver = Qemu(fls_version="0.2.0")
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process()

    with patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls") as mock_get:
        with patch(
            "jumpstarter_driver_qemu.driver.anyio.open_process", new_callable=AsyncMock, return_value=mock_process
        ):
            await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")

            mock_get.assert_called_once_with(
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


def test_flash_oci_via_flasher_client():
    """flasher.flash('oci://...') should route through flash_oci on the driver."""
    mock_process = _create_mock_process(stdout_lines=["done\n"])

    with serve(Qemu()) as qemu:
        with patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"):
            with patch(
                "jumpstarter_driver_qemu.driver.anyio.open_process", new_callable=AsyncMock, return_value=mock_process
            ) as mock_exec:
                qemu.flasher.flash("oci://quay.io/org/image:tag")

                mock_exec.assert_called_once()
                cmd = mock_exec.call_args.args[0]
                assert cmd[1] == "from-url"
                assert cmd[2] == "oci://quay.io/org/image:tag"


def test_flash_oci_convenience_method():
    """qemu.flash_oci() should delegate to flasher.flash()."""
    mock_process = _create_mock_process()

    with serve(Qemu()) as qemu:
        with patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"):
            with patch(
                "jumpstarter_driver_qemu.driver.anyio.open_process", new_callable=AsyncMock, return_value=mock_process
            ) as mock_exec:
                qemu.flash_oci("oci://quay.io/org/image:tag", partition="bios")

                mock_exec.assert_called_once()
                cmd = mock_exec.call_args.args[0]
                assert cmd[1] == "from-url"
                assert cmd[2] == "oci://quay.io/org/image:tag"
                assert Path(cmd[3]).name == "bios"


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
