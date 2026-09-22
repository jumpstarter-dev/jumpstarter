"""Qualcomm QDL flasher and firmware identification drivers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
from pydantic import TypeAdapter

from ..tac import send_tac_sequence
from .executor import execute_manifest, resolve_firmware_root
from .schema import (
    FirmwareManifest,
    find_embedded_manifest,
    load_firmware_manifest,
    load_firmware_manifest_from_mapping,
)
from .soc_profiles import SoCType, get_soc_profile
from jumpstarter.client.flasher import FlashPhase, FlashStatus
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.resources import PresignedRequestResource, Resource
from jumpstarter.driver import Driver, export
from jumpstarter.driver.flasher import StreamingFlasherInterface
from jumpstarter.streams.progress import ProgressAttribute

logger = logging.getLogger(__name__)


@dataclass
class _FlashContext:
    work_dir: Path | None = None
    firmware_root: Path | None = None
    manifest: FirmwareManifest | None = None
    temp_work_dir: Path | None = None
    cache_dir: Path | None = None


@dataclass(kw_only=True)
class QualcommFlasher(StreamingFlasherInterface, Driver):
    """Qualcomm firmware flasher and identification driver using QDL and fastboot."""
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
    tac_command_timeout: float = field(
        default=10.0,
        metadata={"help": "Timeout for each TAC serial command acknowledgement (seconds)"},
    )

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        if "tac" not in self.children:
            raise ConfigurationError("'tac' child is required for QualcommFlasher")
        Path(self.work_dir).mkdir(parents=True, exist_ok=True)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_ridesx.qdl.client.QualcommFlasherClient"

    @property
    def profile(self):
        return get_soc_profile(self.soc_type)

    async def _set_mode(self, mode: str, check_dmesg: str | None = None) -> None:
        from .executor import set_device_mode

        await set_device_mode(
            tac=self.children["tac"],
            profile=self.profile,
            mode=mode,
            check=check_dmesg,
            tac_timeout=self.tac_command_timeout,
        )

    @export
    async def boot_to_edl(self) -> None:
        await self._set_mode("edl")

    @export
    async def boot_to_fastboot(self) -> None:
        await self._set_mode("fastboot")

    @export
    async def power_cycle(self) -> None:
        tac = self.children["tac"]
        await send_tac_sequence(tac, self.profile.power_off_commands, timeout=self.tac_command_timeout)
        await asyncio.sleep(self.power_cycle_delay)
        await send_tac_sequence(tac, self.profile.power_on_commands, timeout=self.tac_command_timeout)

    @staticmethod
    def _validate_member(member: tarfile.TarInfo, extract_root: Path) -> None:
        """Validate a tar member for path traversal and special files (Python <3.12)."""
        if member.name.startswith("/"):
            raise tarfile.ExtractError(f"Blocked absolute path in archive: {member.name}")
        if member.isdev() or member.issym() or member.islnk():
            raise tarfile.ExtractError(f"Blocked special file in archive: {member.name}")
        destination = extract_root.resolve()
        target = (destination / member.name).resolve()
        try:
            target.relative_to(destination)
        except ValueError as exc:
            raise tarfile.ExtractError(
                f"Blocked path traversal in archive: {member.name}"
            ) from exc

    @staticmethod
    def _safe_extractall(archive: tarfile.TarFile, extract_root: Path) -> None:
        archive.extractall(path=extract_root, filter="data")
        return
        destination = extract_root.resolve()
        for member in archive.getmembers():
            QualcommFlasher._validate_member(member, destination)
        archive.extractall(path=extract_root)

    @staticmethod
    def _ensure_firmware_root(work_dir: Path, manifest: FirmwareManifest) -> None:
        """Ensure firmware files end up under ``work_dir/<folder>``.

        After extraction, if the expected folder already exists, the tarball
        was already structured correctly.  Otherwise the tarball has files at
        the root — move them into the folder so ``resolve_firmware_root``
        finds them.
        """
        firmware_root = work_dir / manifest.data.folder
        if firmware_root.is_dir():
            logger.debug("firmware root %s already exists after extraction", firmware_root)
            return
        logger.info("Wrapping extracted files into %s", firmware_root)
        firmware_root.mkdir(parents=True, exist_ok=True)
        for entry in work_dir.iterdir():
            if entry == firmware_root:
                continue
            entry.rename(firmware_root / entry.name)

    def _extract_archive(self, archive_path: Path, work_dir: Path, manifest: FirmwareManifest | None) -> None:
        with tarfile.open(archive_path, mode="r:*") as archive:
            self._safe_extractall(archive, work_dir)
        if manifest:
            self._ensure_firmware_root(work_dir, manifest)

    def _resolve_manifest(self, manifest_data: dict | None, work_dir: Path) -> FirmwareManifest:
        if manifest_data:
            return load_firmware_manifest_from_mapping(manifest_data)
        embedded = find_embedded_manifest(work_dir)
        if embedded is not None:
            return load_firmware_manifest(embedded)
        raise FileNotFoundError(
            "No manifest provided and jumpstarter_manifest.yaml not found in extracted firmware"
        )

    def _firmware_root(self, manifest: FirmwareManifest) -> Path:
        return resolve_firmware_root(Path(self.work_dir), manifest)

    _CACHE_MARKER = ".jumpstarter-cache-complete"

    def _cache_is_valid(self, firmware_root: Path) -> bool:
        """A cache is valid only when the completion marker exists."""
        return firmware_root.is_dir() and (firmware_root / self._CACHE_MARKER).is_file()

    def _read_cache_marker(self, firmware_root: Path) -> dict[str, Any] | None:
        """Read the cache marker metadata, or None if absent/corrupt."""
        marker = firmware_root / self._CACHE_MARKER
        if not marker.is_file():
            return None
        try:
            return json.loads(marker.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache_marker(
        self,
        firmware_root: Path,
        *,
        source_filename: str | None = None,
        http_metadata: dict[str, str] | None = None,
        local_metadata: dict[str, str] | None = None,
    ) -> None:
        """Write a completion marker after a successful extraction."""
        marker = firmware_root / self._CACHE_MARKER
        metadata: dict[str, Any] = {"version": 1}
        if source_filename:
            metadata["source_filename"] = source_filename
        if http_metadata:
            metadata["http"] = http_metadata
        if local_metadata:
            metadata["local"] = local_metadata
        marker.write_text(json.dumps(metadata), encoding="utf-8")

    @staticmethod
    def _extract_source_url(source: Any) -> str | None:
        """Extract the URL from a PresignedRequestResource handle, or None."""
        with contextlib.suppress(Exception):
            handle = TypeAdapter(Resource).validate_python(source)
            if isinstance(handle, PresignedRequestResource) and handle.method == "GET":
                return handle.url
        return None

    @staticmethod
    async def _http_head_metadata(url: str) -> dict[str, str]:
        """Fetch ETag/Last-Modified/Content-Length via a HEAD request."""
        metadata: dict[str, str] = {}
        async with (
            aiohttp.ClientSession() as session,
            session.head(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=15)) as resp,
        ):
            if resp.status == 200:
                for key in ("ETag", "Last-Modified", "Content-Length"):
                    value = resp.headers.get(key)
                    if value:
                        metadata[key] = value
        return metadata

    @staticmethod
    def _local_file_metadata(path: str) -> dict[str, str] | None:
        if path.startswith(("http://", "https://")):
            return None
        file_path = Path(path)
        if not file_path.is_file():
            return None
        stat = file_path.stat()
        return {
            "path": str(file_path.resolve()),
            "mtime": str(stat.st_mtime_ns),
            "size": str(stat.st_size),
        }

    async def _cache_is_fresh(
        self, firmware_root: Path, source: Any, source_filename: str | None = None,
    ) -> bool:
        """Check whether the cached firmware is still fresh.

        For HTTP sources, compares stored HEAD metadata against the server.
        For local sources, compares stored mtime/size metadata against the file.
        """
        marker_data = self._read_cache_marker(firmware_root)
        if marker_data is None:
            return False

        stored_local = marker_data.get("local")
        current_local = self._local_file_metadata(source_filename) if source_filename else None
        if stored_local and current_local:
            for key in ("mtime", "size"):
                stored_val = stored_local.get(key)
                current_val = current_local.get(key)
                if stored_val and current_val and stored_val != current_val:
                    logger.info("Cache stale: local %s changed (%s -> %s)", key, stored_val, current_val)
                    return False
            return True

        stored_http = marker_data.get("http")
        if not stored_http:
            return True
        url = self._extract_source_url(source)
        if url is None:
            return True
        try:
            current = await self._http_head_metadata(url)
        except Exception:
            logger.debug("HEAD request failed for cache freshness check; assuming fresh")
            return True
        for key in ("ETag", "Last-Modified", "Content-Length"):
            stored_val = stored_http.get(key)
            current_val = current.get(key)
            if stored_val and current_val and stored_val != current_val:
                logger.info("Cache stale: %s changed (%s -> %s)", key, stored_val, current_val)
                return False
        return True

    @staticmethod
    def _validate_source_id(source_id: str) -> None:
        """Reject source_id values that could escape the cache directory."""
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", source_id) or source_id in (".", ".."):
            raise ValueError(f"invalid source_id: {source_id!r}")

    def _cache_work_dir(self, source_id: str | None) -> Path:
        """Return the cache directory for a given source, namespaced by source_id."""
        base = Path(self.work_dir)
        if not source_id:
            return base
        self._validate_source_id(source_id)
        candidate = (base / source_id).resolve()
        if not candidate.is_relative_to(base.resolve()):
            raise ValueError(f"invalid source_id: {source_id!r}")
        return candidate

    def _clear_cache(self, work_dir: Path, manifest: FirmwareManifest | None) -> None:
        """Remove cached firmware for a forced re-download."""
        if manifest is not None:
            firmware_root = resolve_firmware_root(work_dir, manifest)
            if firmware_root.exists():
                logger.info("Force download: removing cached firmware at %s", firmware_root)
                shutil.rmtree(firmware_root, ignore_errors=True)
        else:
            logger.info("Force download: clearing cache at %s", work_dir)
            shutil.rmtree(work_dir, ignore_errors=True)
            work_dir.mkdir(parents=True, exist_ok=True)

    def _manifest_from_work_dir(self, work_dir: Path) -> FirmwareManifest | None:
        embedded = find_embedded_manifest(work_dir)
        if embedded is None:
            return None
        return load_firmware_manifest(embedded)

    async def _try_cache_hit(
        self,
        work_dir: Path,
        manifest: FirmwareManifest,
        source: Any,
        ctx: _FlashContext,
        *,
        source_filename: str | None = None,
    ) -> FlashStatus | None:
        """Return a FlashStatus if the cache is valid and fresh, else None."""
        firmware_root = resolve_firmware_root(work_dir, manifest)
        if not self._cache_is_valid(firmware_root):
            return None
        if not await self._cache_is_fresh(firmware_root, source, source_filename=source_filename):
            logger.info("Cache stale: removing %s", firmware_root)
            shutil.rmtree(firmware_root, ignore_errors=True)
            return None
        ctx.work_dir = work_dir
        ctx.firmware_root = firmware_root
        ctx.manifest = manifest
        ctx.cache_dir = firmware_root
        return FlashStatus(phase=FlashPhase.CACHE, message=f"Using cached firmware at {firmware_root}")

    async def _collect_source_metadata(self, source: Any) -> dict[str, str] | None:
        """Collect HTTP metadata from the source for cache freshness tracking."""
        source_url = self._extract_source_url(source)
        if not source_url:
            return None
        try:
            return await self._http_head_metadata(source_url)
        except Exception:
            logger.debug("Failed to collect HTTP metadata for cache marker")
            return None

    async def _prepare_cached_flash(
        self,
        source: Any,
        manifest_data: dict | None,
        ctx: _FlashContext,
        *,
        source_id: str | None = None,
        force_download: bool = False,
        source_filename: str | None = None,
    ) -> AsyncGenerator[FlashStatus, None]:
        work_dir = self._cache_work_dir(source_id)
        work_dir.mkdir(parents=True, exist_ok=True)

        manifest = load_firmware_manifest_from_mapping(manifest_data) if manifest_data else None
        if manifest is None:
            manifest = self._manifest_from_work_dir(work_dir)

        if force_download:
            self._clear_cache(work_dir, manifest)

        if manifest is not None:
            hit = await self._try_cache_hit(
                work_dir, manifest, source, ctx, source_filename=source_filename,
            )
            if hit is not None:
                yield hit
                return

        if source is None:
            if manifest is not None:
                firmware_root = resolve_firmware_root(work_dir, manifest)
                raise FileNotFoundError(
                    f"No cached firmware at {firmware_root}; provide a firmware archive to populate the cache"
                )
            raise ValueError("firmware source is required when no manifest is provided")

        if manifest is not None:
            ctx.cache_dir = resolve_firmware_root(work_dir, manifest)

        http_metadata = await self._collect_source_metadata(source)
        local_metadata = self._local_file_metadata(source_filename) if source_filename else None

        async for status in self._download_and_extract(source, work_dir, manifest, source_filename=source_filename):
            yield status

        if manifest is None:
            manifest = self._resolve_manifest(None, work_dir)

        firmware_root = resolve_firmware_root(work_dir, manifest)
        self._write_cache_marker(
            firmware_root,
            source_filename=source_filename,
            http_metadata=http_metadata,
            local_metadata=local_metadata,
        )
        ctx.work_dir = work_dir
        ctx.firmware_root = firmware_root
        ctx.manifest = manifest
        ctx.cache_dir = firmware_root
        yield FlashStatus(phase=FlashPhase.CACHE, message=f"Cached firmware at {firmware_root}")

    async def _prepare_ephemeral_flash(
        self,
        source: Any,
        manifest_data: dict | None,
        ctx: _FlashContext,
        source_filename: str | None = None,
    ) -> AsyncGenerator[FlashStatus, None]:
        if source is None:
            raise ValueError("firmware source is required")
        temp_work_dir = Path(tempfile.mkdtemp(prefix="qcom-flash-", dir=self.work_dir))
        ctx.temp_work_dir = temp_work_dir
        ctx.work_dir = temp_work_dir
        manifest_hint = load_firmware_manifest_from_mapping(manifest_data) if manifest_data else None
        async for status in self._download_and_extract(
            source, temp_work_dir, manifest_hint, source_filename=source_filename,
        ):
            yield status
        ctx.manifest = self._resolve_manifest(manifest_data, temp_work_dir)
        ctx.firmware_root = resolve_firmware_root(temp_work_dir, ctx.manifest)

    @staticmethod
    def _build_tar_cmd(extract_root: Path, decompress_flag: str | None) -> list[str]:
        """Build the tar command with the appropriate decompression flag."""
        if decompress_flag and decompress_flag.startswith("--"):
            return ["tar", decompress_flag, "-xf", "-", "-C", str(extract_root)]
        elif decompress_flag:
            return ["tar", f"-x{decompress_flag.lstrip('-')}f", "-", "-C", str(extract_root)]
        return ["tar", "-xf", "-", "-C", str(extract_root)]

    @staticmethod
    def _detect_compression(header: bytes) -> str | None:
        """Detect compression format from the first bytes of a stream.

        Returns the tar flag (-J, -z, -j, --zstd) or None for plain tar.
        This is more reliable than filename-based detection since files
        may have misleading extensions.
        """
        if header[:6] == b"\xfd7zXZ\x00":      # xz magic
            return "-J"
        if header[:2] == b"\x1f\x8b":           # gzip magic
            return "-z"
        if header[:3] == b"BZh":                # bzip2 magic
            return "-j"
        if header[:4] == b"\x28\xb5\x2f\xfd":  # zstd magic
            return "--zstd"
        return None

    @staticmethod
    def _start_tar(header: bytes, extract_root: Path) -> subprocess.Popen:
        """Start a tar subprocess with compression detected from magic bytes.

        stdout is discarded (tar -x produces no useful output).
        stderr goes to a temp file so the OS drains it, avoiding pipe-buffer
        deadlocks when tar emits many warnings during large extractions.
        """
        decompress_flag = QualcommFlasher._detect_compression(header)
        tar_cmd = QualcommFlasher._build_tar_cmd(extract_root, decompress_flag)
        logger.info("Running: %s", " ".join(tar_cmd))
        stderr_file = tempfile.TemporaryFile()  # noqa: SIM115
        return subprocess.Popen(
            tar_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr_file,
        )

    @staticmethod
    def _finish_tar(tar_proc: subprocess.Popen, chunk_count: int, bytes_received: int) -> None:
        """Wait for tar to finish and raise on failure."""
        tar_proc.wait()
        stderr_output = ""
        if tar_proc.stderr is not None:
            tar_proc.stderr.seek(0)
            stderr_output = tar_proc.stderr.read().decode(errors="replace").strip()
            tar_proc.stderr.close()
        logger.info(
            "tar finished: rc=%s, chunks=%d, bytes=%d, stderr=%s",
            tar_proc.returncode, chunk_count, bytes_received,
            stderr_output or "(empty)",
        )
        if tar_proc.returncode != 0:
            raise RuntimeError(
                f"tar extraction failed (rc={tar_proc.returncode}): {stderr_output}"
            )

    class _StreamToTarResult:
        bytes_received: int = 0

    @staticmethod
    async def _write_to_tar(proc: subprocess.Popen, data: bytes) -> bool:
        """Write data to tar stdin; returns False on BrokenPipeError."""
        assert proc.stdin is not None, "tar process must be started with stdin=PIPE"
        try:
            await asyncio.to_thread(proc.stdin.write, data)
            return True
        except BrokenPipeError:
            return False

    _WRITE_THRESHOLD = 1024 * 1024  # 1 MB
    _MIN_HEADER_BYTES = 6  # Enough to detect xz, gzip, bzip2, zstd magic.

    async def _flush_tar_buffer(
        self, tar_proc: subprocess.Popen | None, write_buf: bytearray, extract_root: Path,
    ) -> subprocess.Popen:
        """Flush remaining buffer into tar, starting the process if needed."""
        if tar_proc is None:
            tar_proc = self._start_tar(bytes(write_buf[:6]), extract_root)
        await self._write_to_tar(tar_proc, bytes(write_buf))
        return tar_proc

    async def _stream_to_tar(
        self,
        res: Any,
        extract_root: Path,
        bytes_total: int | None,
        result: _StreamToTarResult,
    ) -> AsyncGenerator[FlashStatus, None]:
        """Pipe a resource stream into tar, detecting compression from magic bytes."""
        tar_proc: subprocess.Popen | None = None
        write_buf = bytearray()
        last_update = time.monotonic()
        try:
            async for chunk in res:
                write_buf.extend(chunk)
                result.bytes_received += len(chunk)
                if tar_proc is None and len(write_buf) >= self._MIN_HEADER_BYTES:
                    tar_proc = self._start_tar(bytes(write_buf[:6]), extract_root)
                if tar_proc is not None and len(write_buf) >= self._WRITE_THRESHOLD:
                    if not await self._write_to_tar(tar_proc, bytes(write_buf)):
                        break
                    write_buf.clear()
                now = time.monotonic()
                if now - last_update >= 0.5:
                    last_update = now
                    yield FlashStatus(
                        phase=FlashPhase.DOWNLOAD,
                        message="Downloading and extracting firmware",
                        bytes_transferred=result.bytes_received,
                        bytes_total=bytes_total,
                    )
            if write_buf:
                tar_proc = await self._flush_tar_buffer(tar_proc, write_buf, extract_root)
        finally:
            if tar_proc is not None:
                try:
                    tar_proc.stdin.close()
                except BrokenPipeError:
                    pass

        if tar_proc is None:
            raise RuntimeError("No data received from firmware source")
        await asyncio.to_thread(
            self._finish_tar, tar_proc, result.bytes_received // self._WRITE_THRESHOLD, result.bytes_received,
        )

    async def _download_and_extract(
        self,
        source: Any,
        work_dir: Path,
        manifest: FirmwareManifest | None,
        source_filename: str | None = None,
    ) -> AsyncGenerator[FlashStatus, None]:
        yield FlashStatus(phase=FlashPhase.DOWNLOAD, message="Downloading and extracting firmware", progress=0.0)
        logger.info("Streaming firmware archive to %s", work_dir)
        bytes_received = 0

        extract_root = work_dir

        async with self.resource(source) as res:
            bytes_total = int(res.extra(ProgressAttribute.total)) if res.extra(ProgressAttribute.total, None) else None
            result = self._StreamToTarResult()
            async for status in self._stream_to_tar(res, extract_root, bytes_total, result):
                yield status
            bytes_received = result.bytes_received

        logger.info("Download and extraction complete: %d bytes received", bytes_received)
        if manifest:
            self._ensure_firmware_root(work_dir, manifest)
        yield FlashStatus(
            phase=FlashPhase.DOWNLOAD,
            message="Download and extraction complete",
            bytes_transferred=bytes_received,
            bytes_total=bytes_total,
        )

    async def _run_manifest_flash(
        self,
        manifest: FirmwareManifest,
        firmware_root: Path,
    ) -> AsyncGenerator[FlashStatus, None]:
        async for status in execute_manifest(
            manifest,
            tac=self.children["tac"],
            profile=self.profile,
            firmware_root=firmware_root,
            qdl_timeout=self.qdl_timeout,
            fastboot_timeout=self.fastboot_timeout,
            tac_timeout=self.tac_command_timeout,
            board_revision=self.board_revision,
        ):
            yield status

        yield FlashStatus(phase=FlashPhase.COMPLETE, message=f"Firmware update complete: {manifest.name}")

    @export
    async def flash(
        self,
        source: Any,
        manifest_data: dict | None = None,
        cached: bool = False,
        source_id: str | None = None,
        force_download: bool = False,
        source_filename: str | None = None,
    ) -> AsyncGenerator[FlashStatus, None]:
        logger.info(
            "flash() called: cached=%s, source_id=%r, force_download=%s, manifest_data=%s, source_filename=%r",
            cached, source_id, force_download, bool(manifest_data), source_filename,
        )
        ctx = _FlashContext()
        try:
            if cached:
                async for status in self._prepare_cached_flash(
                    source, manifest_data, ctx,
                    source_id=source_id, force_download=force_download,
                    source_filename=source_filename,
                ):
                    yield status
            else:
                async for status in self._prepare_ephemeral_flash(
                    source, manifest_data, ctx, source_filename=source_filename,
                ):
                    yield status

            assert ctx.manifest is not None
            assert ctx.firmware_root is not None
            async for status in self._run_manifest_flash(
                ctx.manifest, ctx.firmware_root,
            ):
                yield status
        except Exception as exc:
            yield FlashStatus(phase=FlashPhase.ERROR, message=str(exc))
            if ctx.cache_dir is not None and not self._cache_is_valid(ctx.cache_dir):
                shutil.rmtree(ctx.cache_dir, ignore_errors=True)
            return
        finally:
            if ctx.temp_work_dir is not None:
                shutil.rmtree(ctx.temp_work_dir, ignore_errors=True)
