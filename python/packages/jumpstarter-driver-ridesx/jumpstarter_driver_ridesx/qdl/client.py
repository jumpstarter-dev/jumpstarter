"""Qualcomm driver clients."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import click
import yaml
from jumpstarter_driver_composite.client import CompositeClient

from .firmware_id import collect_version_info, format_version_report
from .schema import load_firmware_manifest_from_mapping
from jumpstarter.client.decorators import driver_click_group
from jumpstarter.client.flasher import (
    FlashStatus,
    StreamingFlasherClient,
    _http_url_adapter,
    _local_file_adapter,
    _parse_path,
)


def _load_manifest_source(source: str) -> dict[str, Any]:
    if source.startswith(("http://", "https://")):
        with urlopen(source) as response:
            raw = yaml.safe_load(response.read().decode("utf-8"))
    else:
        raw = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise click.ClickException("Manifest must be a YAML mapping")
    manifest = load_firmware_manifest_from_mapping(raw)
    return manifest.model_dump(mode="json")


def _manifest_data_from_source(manifest: Any | None) -> dict[str, Any] | None:
    if manifest is None:
        return None
    if isinstance(manifest, dict):
        return manifest
    if isinstance(manifest, str):
        return _load_manifest_source(manifest)
    return load_firmware_manifest_from_mapping(manifest).model_dump(mode="json")


@dataclass(kw_only=True)
class QualcommFlasherClient(StreamingFlasherClient, CompositeClient):
    """Client for Qualcomm QDL firmware flashing and identification."""

    def _iter_flash_status(
        self,
        *,
        handle: Any,
        manifest: dict[str, Any] | None,
        cached: bool,
    ):
        for value in self.streamingcall("flash", handle, manifest, cached):
            status = FlashStatus.model_validate(value, strict=True)
            yield status
            if status.phase == "error":
                raise RuntimeError(status.message)

    def flash_stream(
        self,
        path=None,
        *,
        manifest: Any | None = None,
        cached: bool = False,
        compression=None,
    ):
        manifest_data = _manifest_data_from_source(manifest)
        if cached and manifest_data is None:
            raise ValueError("manifest is required when using --cached")

        if cached and path is None:
            yield from self._iter_flash_status(handle=None, manifest=manifest_data, cached=True)
            return

        if path is None:
            raise ValueError("firmware file or URL is required")

        local_path, url = _parse_path(path)
        if url is not None:
            with _http_url_adapter(client=self, url=url, mode="rb") as handle:
                yield from self._iter_flash_status(handle=handle, manifest=manifest_data, cached=cached)
        else:
            with _local_file_adapter(client=self, path=local_path, mode="rb", compression=compression) as handle:
                yield from self._iter_flash_status(handle=handle, manifest=manifest_data, cached=cached)

    def identify(self, *, verbose: bool = False, capture_dir: str | None = None):
        for child in ("serial", "sail"):
            if child not in self.children:
                raise click.ClickException(
                    f"'{child}' child is required for firmware identification; add it to the exporter config"
                )
        with self.serial.pexpect() as serial, self.sail.pexpect() as sail:
            return collect_version_info(
                serial,
                sail,
                lambda: self.call("power_cycle"),
                verbose=verbose,
                capture_dir=capture_dir,
            )

    def cli(self):
        @driver_click_group(self)
        def base():
            """Qualcomm firmware flasher"""
            pass

        @base.command()
        @click.argument("file", required=False, metavar="FILE|URL")
        @click.option(
            "--manifest",
            type=str,
            help=(
                "Manifest YAML file or http(s) URL. Required with --cached. "
                "Optional when the archive contains jumpstarter_manifest.yaml."
            ),
        )
        @click.option(
            "--cached",
            is_flag=True,
            help="Keep extracted firmware on the exporter and reuse it on subsequent flashes.",
        )
        def flash(file, manifest, cached):
            """Flash firmware using QDL from a local path or http(s) URL"""
            if cached and not manifest:
                raise click.ClickException("--manifest is required when using --cached")
            if not cached and not file:
                raise click.ClickException(
                    "FILE|URL is required unless re-flashing from an existing cache with --cached"
                )
            try:
                for status in self.flash_stream(file, manifest=manifest, cached=cached):
                    click.echo(self.render_flash_status(status))
            except (RuntimeError, ValueError, FileNotFoundError) as exc:
                raise click.ClickException(str(exc)) from exc

        @base.command()
        @click.option("-v", "--verbose", is_flag=True, help="Print progress messages")
        @click.option("--capture", type=click.Path(), help="Directory to save raw serial logs")
        def id(verbose, capture):
            """Identify firmware variant and version strings"""
            versions = self.identify(verbose=verbose, capture_dir=capture)
            click.echo(format_version_report(versions))

        @base.command("boot-to-edl")
        def boot_to_edl():
            """Boot the device into EDL mode"""
            self.call("boot_to_edl")

        @base.command("boot-to-fastboot")
        def boot_to_fastboot():
            """Boot the device into fastboot mode"""
            self.call("boot_to_fastboot")

        return base
