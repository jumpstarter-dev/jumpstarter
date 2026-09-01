"""Qualcomm driver clients."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import click
import yaml
from jumpstarter_driver_composite.client import CompositeClient
from rich.console import Console, Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)
from rich.text import Text

from .firmware_id import collect_version_info, format_version_report
from .schema import load_firmware_manifest_from_mapping
from jumpstarter.client.decorators import driver_click_group
from jumpstarter.client.flasher import (
    FlashPhase,
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


class _FlashPanel:
    """Live-updating panel for firmware flash progress."""

    def __init__(self, console: Console) -> None:
        self._console = console
        self._overall = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        )
        self._download = Progress(
            TextColumn("  "),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeElapsedColumn(),
            console=console,
        )
        self._overall_task = self._overall.add_task("[bold]Flashing firmware", total=None)
        self._download_task = self._download.add_task("download", total=None, visible=False)
        self._status_text = Text()
        self._log_lines: list[Text] = []
        self._live = Live(self._build(), console=console, refresh_per_second=8)

    def _build(self) -> Group:
        parts: list[Any] = list(self._log_lines)
        parts.append(self._overall)
        if self._download.tasks[self._download_task].visible:
            parts.append(self._download)
        if self._status_text:
            parts.append(self._status_text)
        return Group(*parts)

    def _log(self, text: Text) -> None:
        self._log_lines.append(text)

    def _refresh(self) -> None:
        self._live.update(self._build())

    def start(self) -> None:
        self._live.start()

    def stop(self) -> None:
        self._live.stop()

    def update(self, status: FlashStatus) -> None:
        if status.phase == FlashPhase.DOWNLOAD:
            self._handle_download(status)
        elif status.phase == FlashPhase.EXTRACT:
            self._hide_download()
            self._status_text = Text.assemble(("  Extracting ", "yellow"), "firmware archive...")
        elif status.phase == FlashPhase.CACHE:
            self._hide_download()
            self._status_text = Text.assemble(("  Cache ", "cyan"), status.message)
        elif status.phase == FlashPhase.STEP:
            self._handle_step(status)
        elif status.phase == FlashPhase.COMPLETE:
            self._handle_complete(status)
        self._refresh()

    def _handle_download(self, status: FlashStatus) -> None:
        task = self._download.tasks[self._download_task]
        if not task.visible:
            self._download.update(self._download_task, visible=True)
            self._overall.update(self._overall_task, description="[bold blue]Downloading firmware")
        if status.bytes_total and task.total is None:
            self._download.update(self._download_task, total=status.bytes_total)
        if status.bytes_transferred is not None:
            self._download.update(self._download_task, completed=status.bytes_transferred)

    def _hide_download(self) -> None:
        if self._download.tasks[self._download_task].visible:
            self._download.update(self._download_task, visible=False)
            self._log(Text.assemble(("  \u2714 ", "green"), "Download complete"))

    def _handle_step(self, status: FlashStatus) -> None:
        self._hide_download()
        if status.step_index is not None and status.total_steps is not None:
            self._overall.update(
                self._overall_task,
                description="[bold]Flashing firmware",
                completed=status.step_index,
                total=status.total_steps,
            )
        if status.message.startswith("Completed ") or status.message.startswith("Completed ABL"):
            self._log(Text.assemble(("  \u2714 ", "green"), status.message.removeprefix("Completed ")))
            self._status_text = Text()
        else:
            label = status.message.removeprefix("Running ")
            self._status_text = Text.assemble(("  \u25b6 ", "blue"), label)

    def _handle_complete(self, status: FlashStatus) -> None:
        self._overall.update(self._overall_task, description="[bold green]Done", completed=100, total=100)
        self._status_text = Text()
        self._log(Text.assemble(("\u2714 ", "bold green"), status.message))


def _flash_rich(client: QualcommFlasherClient, file, *, manifest, cached) -> None:
    """Render flash progress using a live-updating rich panel."""
    console = Console(stderr=True)
    panel = _FlashPanel(console)
    panel.start()
    try:
        for status in client.flash_stream(file, manifest=manifest, cached=cached):
            panel.update(status)
    finally:
        panel.stop()


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
            status = FlashStatus.model_validate(value)
            yield status
            if status.phase == FlashPhase.ERROR:
                raise RuntimeError(status.message)

    def flash_stream(
        self,
        path,
        *,
        manifest: Any | None = None,
        cached: bool = False,
        compression=None,
    ):
        manifest_data = _manifest_data_from_source(manifest)

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
        @click.argument("file", metavar="FILE|URL")
        @click.option(
            "--manifest",
            type=str,
            help=(
                "Manifest YAML file or http(s) URL. "
                "Optional when the archive contains jumpstarter_manifest.yaml."
            ),
        )
        @click.option(
            "--cached",
            is_flag=True,
            help=(
                "Reuse previously downloaded firmware if present on the exporter "
                "and keep extracted files on disk after flashing."
            ),
        )
        def flash(file, manifest, cached):
            """Flash firmware using QDL from a local path or http(s) URL"""
            use_rich = sys.stderr.isatty()
            try:
                if use_rich:
                    _flash_rich(self, file, manifest=manifest, cached=cached)
                else:
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
