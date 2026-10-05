from pathlib import Path

import pytest
import yaml

from jumpstarter_driver_ridesx.qdl.driver import QualcommFlasher, _FlashContext
from jumpstarter_driver_ridesx.qdl.schema import (
    FirmwareData,
    FirmwareManifest,
    find_embedded_manifest,
    load_firmware_manifest,
)

from jumpstarter.client.flasher import FlashPhase


def test_cache_is_valid(tmp_path):
    driver = QualcommFlasher.__new__(QualcommFlasher)
    driver.work_dir = str(tmp_path)
    manifest = load_firmware_manifest(Path(__file__).parent / "examples" / "manifests" / "es22.yaml")
    firmware_root = driver._firmware_root(manifest)
    assert not driver._cache_is_valid(firmware_root)

    firmware_root.mkdir(parents=True)
    (firmware_root / "ufs").mkdir()
    # Cache is only valid when the marker file is present
    assert not driver._cache_is_valid(firmware_root)

    (firmware_root / driver._CACHE_MARKER).write_text("{}")
    assert driver._cache_is_valid(firmware_root)


def test_cache_is_valid_empty_directory(tmp_path):
    driver = QualcommFlasher.__new__(QualcommFlasher)
    driver.work_dir = str(tmp_path)
    manifest = FirmwareManifest(name="test", data=FirmwareData(folder="empty"), steps=[])
    firmware_root = driver._firmware_root(manifest)
    firmware_root.mkdir(parents=True)
    # Empty directory without marker is not valid
    assert not driver._cache_is_valid(firmware_root)


def test_find_embedded_manifest(tmp_path):
    nested = tmp_path / "release"
    nested.mkdir()
    (nested / "jumpstarter_manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "embedded",
                "data": {"folder": "release"},
                "steps": [],
            }
        ),
        encoding="utf-8",
    )

    manifest_path = find_embedded_manifest(tmp_path)
    assert manifest_path is not None
    assert manifest_path.name == "jumpstarter_manifest.yaml"
    assert load_firmware_manifest(manifest_path).name == "embedded"


def test_find_embedded_manifest_ignores_manifest_yaml(tmp_path):
    nested = tmp_path / "release"
    nested.mkdir()
    (nested / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "legacy",
                "data": {"folder": "release"},
                "steps": [],
            }
        ),
        encoding="utf-8",
    )

    assert find_embedded_manifest(tmp_path) is None


def test_resolve_manifest_from_embedded_jumpstarter_manifest(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    manifest = {
        "name": "embedded",
        "data": {"folder": "r00002.2a_AWE"},
        "steps": [{"sleep": 1}],
    }
    (work_dir / "jumpstarter_manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    driver = QualcommFlasher.__new__(QualcommFlasher)
    resolved = driver._resolve_manifest(None, work_dir)
    assert resolved.name == "embedded"


@pytest.mark.asyncio
async def test_cache_is_fresh_detects_local_file_change(tmp_path):
    driver = QualcommFlasher.__new__(QualcommFlasher)
    driver.work_dir = str(tmp_path)
    firmware_root = tmp_path / "cache"
    firmware_root.mkdir()
    archive = tmp_path / "firmware.tar"
    archive.write_bytes(b"firmware")
    stat = archive.stat()
    driver._write_cache_marker(
        firmware_root,
        source_filename=str(archive),
        local_metadata={
            "path": str(archive.resolve()),
            "mtime": str(stat.st_mtime_ns),
            "size": str(stat.st_size),
        },
    )

    assert await driver._cache_is_fresh(firmware_root, None, source_filename=str(archive))

    archive.write_bytes(b"updated firmware")
    assert not await driver._cache_is_fresh(firmware_root, None, source_filename=str(archive))


@pytest.mark.asyncio
async def test_prepare_cached_flash_uses_embedded_manifest_without_manifest_data(tmp_path):
    driver = QualcommFlasher.__new__(QualcommFlasher)
    driver.work_dir = str(tmp_path)
    work_dir = tmp_path / "cache123"
    work_dir.mkdir()
    manifest = {
        "name": "embedded",
        "data": {"folder": "release"},
        "steps": [{"sleep": 1}],
    }
    (work_dir / "jumpstarter_manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    firmware_root = work_dir / "release"
    firmware_root.mkdir()
    (firmware_root / "ufs").mkdir()
    driver._write_cache_marker(firmware_root)

    ctx = _FlashContext()
    statuses = []
    async for status in driver._prepare_cached_flash(
        None, None, ctx, source_id="cache123",
    ):
        statuses.append(status)

    assert len(statuses) == 1
    assert statuses[0].phase == FlashPhase.CACHE
    assert ctx.manifest is not None
    assert ctx.manifest.name == "embedded"


