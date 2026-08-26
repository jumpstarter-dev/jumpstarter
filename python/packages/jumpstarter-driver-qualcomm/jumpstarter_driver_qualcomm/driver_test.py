from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jumpstarter_driver_qualcomm.driver import QualcommFlasher
from jumpstarter_driver_qualcomm.executor import execute_manifest
from jumpstarter_driver_qualcomm.schema import SleepStep, load_firmware_manifest
from jumpstarter_driver_qualcomm.soc_profiles import SA8775P

from jumpstarter.common.exceptions import ConfigurationError


@pytest.mark.asyncio
async def test_execute_manifest_sleep_step():
    manifest = load_firmware_manifest(Path(__file__).parent / "examples" / "manifests" / "es22.yaml")
    manifest = type(manifest)(**{**manifest.model_dump(), "steps": [SleepStep(sleep=0)]})
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
        )
    ]
    assert statuses[0].phase == "step"
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
