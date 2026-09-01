import subprocess
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jumpstarter_driver_ridesx.qdl.driver import QualcommFlasher
from jumpstarter_driver_ridesx.qdl.executor import execute_manifest
from jumpstarter_driver_ridesx.qdl.schema import (
    FirmwareData,
    FirmwareManifest,
    SleepStep,
    load_firmware_manifest,
)
from jumpstarter_driver_ridesx.qdl.soc_profiles import SA8775P

from jumpstarter.client.flasher import FlashPhase
from jumpstarter.common.exceptions import ConfigurationError


@pytest.mark.asyncio
async def test_execute_manifest_sleep_step():
    manifest = load_firmware_manifest(Path(__file__).parent / "examples" / "manifests" / "es22.yaml")
    manifest = manifest.model_copy(update={"steps": [SleepStep(sleep=0)]})
    tac = MagicMock()
    statuses = [
        status
        async for status in execute_manifest(
            manifest,
            tac=tac,
            profile=SA8775P,
            firmware_root=Path("/tmp/firmware"),
            qdl_timeout=1,
            fastboot_timeout=1,
            tac_timeout=1,
        )
    ]
    assert statuses[0].phase == FlashPhase.STEP
    assert statuses[0].step_name == "sleep 0s"


def test_qualcomm_flasher_requires_tac_child():
    with pytest.raises(ConfigurationError, match="tac"):
        QualcommFlasher(children={})


@pytest.mark.asyncio
async def test_qualcomm_flasher_power_cycle(tmp_path):
    mock_tac = MagicMock()
    stream = AsyncMock()
    stream.__aenter__ = AsyncMock(return_value=stream)
    stream.__aexit__ = AsyncMock(return_value=None)
    stream.receive = AsyncMock(return_value=b"ok")
    mock_tac.connect.return_value = stream

    driver = QualcommFlasher(children={"tac": mock_tac}, work_dir=str(tmp_path))
    await driver.power_cycle()
    assert stream.send.await_count > 0


@pytest.mark.asyncio
async def test_qualcomm_flasher_boot_to_edl(tmp_path):
    mock_tac = MagicMock()
    stream = AsyncMock()
    stream.__aenter__ = AsyncMock(return_value=stream)
    stream.__aexit__ = AsyncMock(return_value=None)
    stream.receive = AsyncMock(return_value=b"ok")
    mock_tac.connect.return_value = stream

    driver = QualcommFlasher(children={"tac": mock_tac}, work_dir=str(tmp_path))
    await driver.boot_to_edl()
    assert stream.send.await_count > 0


@pytest.mark.asyncio
async def test_flash_post_steps_ensures_fastboot_before_cdt(tmp_path):
    """_flash_post_steps must re-enter fastboot mode before flashing CDT."""
    mock_tac = MagicMock()
    stream = AsyncMock()
    stream.__aenter__ = AsyncMock(return_value=stream)
    stream.__aexit__ = AsyncMock(return_value=None)
    stream.receive = AsyncMock(return_value=b"ok")
    mock_tac.connect.return_value = stream

    driver = QualcommFlasher(children={"tac": mock_tac}, work_dir=str(tmp_path))

    # Create a CDT file so _find_image_path succeeds
    firmware_root = tmp_path / "fw"
    firmware_root.mkdir()
    cdt_file = firmware_root / "cdt.bin"
    cdt_file.write_bytes(b"\x00")

    manifest = FirmwareManifest(
        name="CS4 test",
        data=FirmwareData(folder="fw", cdt_image={"v4": "cdt.bin"}),
        steps=[],
    )

    fastboot_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with (
        patch.object(driver, "_ensure_fastboot_mode", new_callable=AsyncMock) as mock_ensure,
        patch("jumpstarter_driver_ridesx.qdl.driver.subprocess") as mock_subprocess,
        patch("jumpstarter_driver_ridesx.qdl.executor.read_dmesg", return_value="Product: Android"),
    ):
        mock_subprocess.run.return_value = fastboot_result
        results = await driver._flash_post_steps(manifest, tmp_path, firmware_root)

    mock_ensure.assert_awaited_once()
    assert len(results) == 1
    assert results[0][0] == "cdt"


@pytest.mark.asyncio
async def test_flash_post_steps_skips_fastboot_check_without_cdt(tmp_path):
    """_flash_post_steps should not call _ensure_fastboot_mode when there is no CDT image."""
    mock_tac = MagicMock()
    stream = AsyncMock()
    stream.__aenter__ = AsyncMock(return_value=stream)
    stream.__aexit__ = AsyncMock(return_value=None)
    stream.receive = AsyncMock(return_value=b"ok")
    mock_tac.connect.return_value = stream

    driver = QualcommFlasher(children={"tac": mock_tac}, work_dir=str(tmp_path))

    manifest = FirmwareManifest(
        name="no CDT",
        data=FirmwareData(folder="fw"),
        steps=[],
    )

    with patch.object(driver, "_ensure_fastboot_mode", new_callable=AsyncMock) as mock_ensure:
        results = await driver._flash_post_steps(manifest, tmp_path, tmp_path)

    mock_ensure.assert_not_awaited()
    assert results == []


@pytest.mark.asyncio
async def test_ensure_fastboot_always_power_cycles(tmp_path):
    """_ensure_fastboot_mode must always power-cycle into real fastboot."""
    mock_tac = MagicMock()
    stream = AsyncMock()
    stream.__aenter__ = AsyncMock(return_value=stream)
    stream.__aexit__ = AsyncMock(return_value=None)
    stream.receive = AsyncMock(return_value=b"ok")
    mock_tac.connect.return_value = stream

    driver = QualcommFlasher(children={"tac": mock_tac}, work_dir=str(tmp_path))

    with patch.object(driver, "_set_mode", new_callable=AsyncMock) as mock_set_mode:
        await driver._ensure_fastboot_mode()

    mock_set_mode.assert_awaited_once_with("fastboot", check_dmesg="Product: Android")


def test_safe_extractall_rejects_path_traversal(tmp_path):
    archive_path = tmp_path / "bad.tar"
    extract_root = tmp_path / "extract"
    extract_root.mkdir()
    with tarfile.open(archive_path, "w") as archive:
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = 4
        import io

        archive.addfile(info, io.BytesIO(b"evil"))

    with tarfile.open(archive_path, "r") as archive:
        # Python 3.12+ filter="data" raises OutsideDestinationError (a FilterError),
        # while our 3.11 fallback raises ExtractError.  Both are subclasses of TarError.
        with pytest.raises(tarfile.TarError):
            QualcommFlasher._safe_extractall(archive, extract_root)
