"""Qualcomm QDL flasher and firmware identification drivers."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from anyio.streams.file import FileWriteStream

from .executor import execute_manifest, resolve_firmware_root
from .schema import (
    EMBEDDED_MANIFEST_NAMES,
    FirmwareManifest,
    cdt_images_configured,
    find_embedded_manifest,
    load_firmware_manifest,
    load_firmware_manifest_from_mapping,
    select_cdt_image_path,
)
from .soc_profiles import SoCType, get_soc_profile
from .tac import send_tac_sequence
from jumpstarter.client.flasher import FlashStatus
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export
from jumpstarter.streams.encoding import AutoDecompressIterator


@dataclass
class _FlashContext:
    work_dir: Path | None = None
    firmware_root: Path | None = None
    manifest: FirmwareManifest | None = None
    temp_work_dir: Path | None = None
    cache_dir: Path | None = None


@dataclass(kw_only=True)
class QualcommFlasher(Driver):
    """Qualcomm firmware flasher and identification driver using QDL and fastboot."""

    driver_type = "storage"
    soc_type: SoCType = "sa8775p"
    work_dir: str = field(default="/var/lib/jumpstarter/qualcomm")
    qdl_timeout: int = field(default=30 * 60)
    fastboot_timeout: int = field(default=10 * 60)
    board_revision: str | None = field(
        default=None,
        metadata={"help": "Optional board revision (v1, v2, v3, v4) for CDT image selection"},
    )
    power_cycle_delay: float = field(
        default=2.0,
        metadata={"help": "Delay between power off and on when identifying firmware (seconds)"},
    )

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        if "tac" not in self.children:
            raise ConfigurationError("'tac' child is required for QualcommFlasher")
        Path(self.work_dir).mkdir(parents=True, exist_ok=True)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_qualcomm.client.QualcommFlasherClient"

    @property
    def profile(self):
        return get_soc_profile(self.soc_type)

    async def _set_mode(self, mode: str, check_dmesg: str | None = None) -> None:
        from .executor import set_device_mode

        await set_device_mode(tac=self.children["tac"], profile=self.profile, mode=mode, check=check_dmesg)

    @export
    async def boot_to_edl(self) -> None:
        await self._set_mode("edl")

    @export
    async def boot_to_fastboot(self) -> None:
        await self._set_mode("fastboot")

    @export
    async def power_cycle(self) -> None:
        tac = self.children["tac"]
        await send_tac_sequence(tac, self.profile.power_off_commands)
        await asyncio.sleep(self.power_cycle_delay)
        await send_tac_sequence(tac, self.profile.power_on_commands)

    def _extract_archive(self, archive_path: Path, work_dir: Path, manifest: FirmwareManifest | None) -> None:
        extract_root = work_dir
        if manifest and manifest.data.extract_to_folder:
            extract_root = work_dir / manifest.data.folder
            extract_root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, mode="r:*") as archive:
            archive.extractall(path=extract_root)

    def _load_manifest_from_archive(self, archive_path: Path) -> FirmwareManifest | None:
        with tarfile.open(archive_path, mode="r:*") as archive:
            for filename in EMBEDDED_MANIFEST_NAMES:
                for member in archive.getmembers():
                    if not member.isfile() or Path(member.name).name != filename:
                        continue
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        continue
                    raw = yaml.safe_load(extracted.read().decode("utf-8"))
                    if isinstance(raw, dict):
                        return load_firmware_manifest_from_mapping(raw)
        return None

    def _resolve_manifest(self, manifest_data: dict | None, work_dir: Path) -> FirmwareManifest:
        if manifest_data:
            return load_firmware_manifest_from_mapping(manifest_data)
        embedded = find_embedded_manifest(work_dir)
        if embedded is not None:
            return load_firmware_manifest(embedded)
        raise FileNotFoundError(
            "No manifest provided and jumpstarter_manifest.yaml not found in extracted firmware"
        )

    def _select_cdt_image(self, manifest: FirmwareManifest) -> str | None:
        return select_cdt_image_path(
            manifest.data.cdt_image,
            board_revision=self.board_revision,
            manifest_name=manifest.name,
        )

    def _find_image_path(self, work_dir: Path, firmware_root: Path, relative_path: str) -> Path:
        candidates = [
            firmware_root / relative_path,
            work_dir / relative_path,
            Path(relative_path),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"Image not found: {relative_path}")

    async def _flash_post_steps(self, manifest: FirmwareManifest, work_dir: Path, firmware_root: Path) -> None:
        if manifest.data.abl_image:
            abl_path = self._find_image_path(work_dir, firmware_root, manifest.data.abl_image)
            for slot in ("abl_a", "abl_b"):
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["fastboot", "flash", slot, str(abl_path)],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=self.fastboot_timeout,
                )
                if result.returncode != 0:
                    raise RuntimeError(f"fastboot flash {slot} failed: {result.stderr or result.stdout}")

        cdt_image = self._select_cdt_image(manifest)
        if cdt_image:
            cdt_path = self._find_image_path(work_dir, firmware_root, cdt_image)
            result = await asyncio.to_thread(
                subprocess.run,
                ["fastboot", "flash", "cdt", str(cdt_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=self.fastboot_timeout,
            )
            if result.returncode != 0:
                raise RuntimeError(f"fastboot flash cdt failed: {result.stderr or result.stdout}")

    def _firmware_root(self, manifest: FirmwareManifest) -> Path:
        return resolve_firmware_root(Path(self.work_dir), manifest)

    def _cache_is_valid(self, firmware_root: Path) -> bool:
        return firmware_root.is_dir() and any(firmware_root.iterdir())

    async def _prepare_cached_flash(
        self,
        source: Any,
        manifest_data: dict,
        ctx: _FlashContext,
    ) -> AsyncGenerator[FlashStatus, None]:
        manifest = load_firmware_manifest_from_mapping(manifest_data)
        work_dir = Path(self.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        firmware_root = self._firmware_root(manifest)
        ctx.work_dir = work_dir
        ctx.firmware_root = firmware_root
        ctx.manifest = manifest
        ctx.cache_dir = firmware_root

        if self._cache_is_valid(firmware_root):
            yield FlashStatus(
                phase="cache",
                message=f"Using cached firmware at {firmware_root}",
            )
            return

        if source is None:
            raise FileNotFoundError(
                f"No cached firmware at {firmware_root}; provide a firmware archive to populate the cache"
            )
        async for status in self._download_and_extract(source, work_dir, manifest):
            yield status
        yield FlashStatus(
            phase="cache",
            message=f"Cached firmware at {firmware_root}",
        )

    async def _prepare_ephemeral_flash(
        self,
        source: Any,
        manifest_data: dict | None,
        ctx: _FlashContext,
    ) -> AsyncGenerator[FlashStatus, None]:
        if source is None:
            raise ValueError("firmware source is required")
        temp_work_dir = Path(tempfile.mkdtemp(prefix="qcom-flash-", dir=self.work_dir))
        ctx.temp_work_dir = temp_work_dir
        ctx.work_dir = temp_work_dir
        manifest_hint = load_firmware_manifest_from_mapping(manifest_data) if manifest_data else None
        async for status in self._download_and_extract(source, temp_work_dir, manifest_hint):
            yield status
        ctx.manifest = self._resolve_manifest(manifest_data, temp_work_dir)
        ctx.firmware_root = resolve_firmware_root(temp_work_dir, ctx.manifest)

    async def _download_and_extract(
        self,
        source: Any,
        work_dir: Path,
        manifest: FirmwareManifest | None,
    ) -> AsyncGenerator[FlashStatus, None]:
        archive_path = work_dir / "firmware.tar"
        yield FlashStatus(phase="download", message="Receiving firmware archive", progress=0.0)
        bytes_written = 0
        async with self.resource(source) as res:
            async with await FileWriteStream.from_path(archive_path) as stream:
                async for chunk in AutoDecompressIterator(source=res):
                    await stream.send(chunk)
                    bytes_written += len(chunk)
                    yield FlashStatus(
                        phase="download",
                        message=f"Received {bytes_written} bytes",
                        bytes_transferred=bytes_written,
                    )

        yield FlashStatus(phase="extract", message="Extracting firmware archive")
        extract_manifest = manifest or self._load_manifest_from_archive(archive_path)
        self._extract_archive(archive_path, work_dir, extract_manifest)
        archive_path.unlink(missing_ok=True)

    async def _run_manifest_flash(
        self,
        manifest: FirmwareManifest,
        work_dir: Path,
        firmware_root: Path,
    ) -> AsyncGenerator[FlashStatus, None]:
        async for status in execute_manifest(
            manifest,
            tac=self.children["tac"],
            profile=self.profile,
            firmware_root=firmware_root,
            qdl_timeout=self.qdl_timeout,
            fastboot_timeout=self.fastboot_timeout,
        ):
            yield status

        if manifest.data.abl_image or cdt_images_configured(manifest.data.cdt_image):
            yield FlashStatus(phase="step", message="Flashing ABL/CDT images via fastboot")
            await self._flash_post_steps(manifest, work_dir, firmware_root)

        yield FlashStatus(phase="complete", message=f"Firmware update complete: {manifest.name}")

    @export
    async def flash(
        self,
        source: Any,
        manifest_data: dict | None = None,
        cached: bool = False,
    ) -> AsyncGenerator[FlashStatus, None]:
        ctx = _FlashContext()
        try:
            if cached:
                if not manifest_data:
                    raise ValueError("manifest is required when using cached firmware")
                async for status in self._prepare_cached_flash(source, manifest_data, ctx):
                    yield status
            else:
                async for status in self._prepare_ephemeral_flash(source, manifest_data, ctx):
                    yield status

            assert ctx.manifest is not None
            assert ctx.work_dir is not None
            assert ctx.firmware_root is not None
            async for status in self._run_manifest_flash(ctx.manifest, ctx.work_dir, ctx.firmware_root):
                yield status
        except Exception as exc:
            yield FlashStatus(phase="error", message=str(exc))
            if ctx.cache_dir is not None and not self._cache_is_valid(ctx.cache_dir):
                shutil.rmtree(ctx.cache_dir, ignore_errors=True)
            raise
        finally:
            if ctx.temp_work_dir is not None:
                shutil.rmtree(ctx.temp_work_dir, ignore_errors=True)
