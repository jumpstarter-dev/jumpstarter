"""``FastbootFlasherClient``: the client of :class:`~.driver.FastbootFlasher`.

A flash is a job on the exporter, so this client stages, starts, and follows
jobs, and reports progress as the core ``FlashStatus``: ``flash_stream()``
yields it, ``flash()`` returns the finished job's :class:`~.jobs.JobInfo`, and
``j <flasher> flash`` does the same from the CLI. It adds staging ahead of
time, job control (``watch``, ``status``, ``resume``, ...), entry into
fastboot, ``getvar`` and ``reboot``, and the flasher's children as subcommands.

Flashing is staged on the exporter first, then committed by a job that runs on
the exporter independently of this client and of the lease. Interrupting a
flash (or :meth:`watch`, or ``Ctrl-C`` on the CLI) only detaches; the job
continues.

Sources:

* ``oci://registry/repo:tag``: an OCI bundle, pulled by the exporter.
* a bundle archive (``.tar``, ``.tar.gz``, ``.tar.xz``, ``.tar.zst``, ...):
  a local path (uploaded) or an http(s) URL (downloaded by the exporter).
* a local bundle: a directory holding the manifest, or the manifest file
  itself; the files it references are uploaded.
* images without a manifest, ``{partition: file}`` (``-t partition:file`` on
  the CLI).
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any, cast

import click
import yaml

from .jobs import JobInfo
from .manifest import referenced_files
from jumpstarter.client import DriverClient
from jumpstarter.client.base import StubDriverClient
from jumpstarter.client.decorators import driver_click_group
from jumpstarter.client.flasher import (
    FlashPhase,
    FlashStatus,
    StreamingFlasherClient,
    _http_url_adapter,
    _local_file_adapter,
    _parse_path,
)
from jumpstarter.common.oci import resolve_oci_credentials
from jumpstarter.streams.encoding import Compression

OCI = "oci://"
STAGE = "stage:"
MANIFEST_NAME = "manifest.yaml"
StatusCallback = Callable[[dict[str, Any]], None]
Source = str | PathLike | dict[str, str] | None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _is_url(path: str) -> bool:
    return path.startswith(("http://", "https://"))


@contextmanager
def _plain_errors():
    """Raise the one error behind an upload's task group, not the group: that's what the caller acts on."""
    try:
        yield
    except BaseExceptionGroup as group:
        leaf: BaseException = group
        while isinstance(leaf, BaseExceptionGroup) and len(leaf.exceptions) == 1:
            leaf = leaf.exceptions[0]
        if leaf is group:
            raise
        raise leaf from group


def render(status: dict[str, Any]) -> str:
    return StreamingFlasherClient.render_flash_status(FlashStatus.model_validate(status))


def _load_manifest(manifest: Any) -> Any:
    """A manifest argument as sent to the exporter: a mapping (from a local file or as given), or ``None``."""
    if manifest is None or isinstance(manifest, dict):
        return manifest
    text = str(manifest)
    if "\n" not in text and Path(text).is_file():
        text = Path(text).read_text()
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise click.UsageError("the manifest must be a FlashManifest mapping (YAML), or a path to one")
    return data


@dataclass
class _LocalBundle:
    files: dict[str, str]  # bundle path -> local path or URL
    manifest: str | None = None
    targets: list[list[str]] | None = None


@dataclass(kw_only=True)
class FastbootFlasherClient(DriverClient):
    """Client for the fastboot flasher: staged on the exporter, lease-independent jobs.

    Interrupting a flash (or :meth:`watch`, or ``Ctrl-C`` on the CLI) only
    detaches; the job continues on the exporter.
    """

    def __getattr__(self, name):
        # Children (power, buttons, consoles) as attributes, like CompositeClient.
        try:
            return self.__dict__["children"][name]
        except KeyError:
            raise AttributeError(name) from None

    # -- device ------------------------------------------------------------

    def enter(self, strategy: str | None = None) -> dict[str, Any]:
        """Put the device into fastboot with the exporter's entry strategies."""
        return self.call("enter", strategy)

    def wait_present(self, timeout: float = 0) -> bool:
        """Whether the device is in fastboot, waiting up to ``timeout`` seconds for it."""
        return self.call("wait_present", timeout)

    def getvar(self, name: str) -> str | None:
        """Read a bootloader variable."""
        return self.call("getvar", name)

    def reboot(self, mode: str = "normal") -> None:
        """Reboot from fastboot into ``normal``, ``bootloader``, or ``fastboot``."""
        self.call("reboot", mode)

    # -- flashing ----------------------------------------------------------

    def flash_stream(
        self,
        path: Source = None,
        *,
        manifest: Any | None = None,
        compression: Compression | None = None,
        target: str | None = None,
        stage_id: str | None = None,
        wipe: bool = False,
        job_id: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> Generator[FlashStatus, None, None]:
        """Flash, yielding ``FlashStatus`` updates; raises ``RuntimeError`` if the flash fails.

        ``path`` is any source (see the module docs); ``manifest`` (a local path,
        YAML text, or a mapping) replaces the bundle's own; ``target`` flashes a
        single image to that partition; ``stage_id`` flashes a bundle staged
        earlier. Stopping the iteration detaches: the job continues.
        """
        for raw in self._flash_raw(
            path, manifest=manifest, compression=compression, target=target, stage_id=stage_id,
            wipe=wipe, job_id=job_id, username=username, password=password,
        ):
            status = FlashStatus.model_validate(raw)
            yield status
            if status.phase == FlashPhase.ERROR:
                raise RuntimeError(status.message)

    def flash(
        self,
        path: Source = None,
        *,
        target: str | None = None,
        compression: Compression | None = None,
        manifest: Any | None = None,
        stage_id: str | None = None,
        wipe: bool = False,
        job_id: str | None = None,
        wait: bool = True,
        on_status: StatusCallback | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> JobInfo:
        """Stage, start a job, and (with ``wait``) follow it to the end; return the job's info.

        Raises ``RuntimeError`` if the job (or its exit script) does not succeed.
        With ``wait=False`` it returns as soon as the job has started.
        """
        job_id = job_id or uuid.uuid4().hex[:12]
        if not wait:
            if stage_id is None:
                stage_id = self.stage(path, target=target, manifest=manifest, compression=compression,
                                      username=username, password=password, on_status=on_status)
            return self.start(stage_id, job_id=job_id, wipe=wipe)
        final: dict[str, Any] | None = None
        for raw in self._flash_raw(
            path, manifest=manifest, compression=compression, target=target, stage_id=stage_id,
            wipe=wipe, job_id=job_id, username=username, password=password,
        ):
            if on_status:
                on_status(raw)
            final = raw
        if final is None or final["phase"] not in (FlashPhase.COMPLETE, FlashPhase.ERROR):
            raise RuntimeError("flash ended without a result")
        if final["phase"] == FlashPhase.ERROR:
            raise RuntimeError(f"flash job {job_id} {final['message']}")
        return self.status(job_id)

    def _flash_raw(
        self,
        path: Source,
        *,
        manifest: Any,
        compression: Compression | None,
        target: str | None,
        stage_id: str | None,
        wipe: bool,
        job_id: str | None,
        username: str | None,
        password: str | None,
    ) -> Iterator[dict[str, Any]]:
        if stage_id is not None:
            if path is not None or manifest is not None:
                raise click.UsageError("a staged bundle carries its files and manifest; pass only the stage id")
            yield from self.streamingcall("flash", STAGE + stage_id, None, wipe, job_id)
            return
        path_str = None if path is None or isinstance(path, dict) else str(path)
        if path_str is not None and path_str.startswith(OCI):
            creds = resolve_oci_credentials(path_str, username=username, password=password)
            yield from self.streamingcall(
                "flash", path_str, _load_manifest(manifest), wipe, job_id, creds.username, creds.plain_password
            )
            return
        local = self._local_bundle(path, target, manifest)
        if local is not None:
            staged = None
            for status in self._stage_local(local):
                staged = status.get("stage_id", staged)
                yield status | ({"phase": "cache"} if "stage_id" in status else {})
            yield from self.streamingcall("flash", STAGE + cast(str, staged), None, wipe, job_id)
            return
        with _plain_errors(), self._archive_handle(cast(str, path_str), compression) as handle:
            yield from self.streamingcall("flash", handle, _load_manifest(manifest), wipe, job_id)

    # -- staging -----------------------------------------------------------

    def stage(
        self,
        path: Source = None,
        *,
        target: str | None = None,
        manifest: Any | None = None,
        compression: Compression | None = None,
        username: str | None = None,
        password: str | None = None,
        on_status: StatusCallback | None = None,
    ) -> str:
        """Copy a bundle (any source) to the exporter and verify it, without touching the device.

        Returns the stage id, for ``flash(stage_id=...)``.
        """
        path_str = None if path is None or isinstance(path, dict) else str(path)
        if path_str is not None and path_str.startswith(OCI):
            creds = resolve_oci_credentials(path_str, username=username, password=password)
            statuses = self.streamingcall("stage", path_str, _load_manifest(manifest), creds.username,
                                          creds.plain_password)
            return self._consume(statuses, on_status)
        local = self._local_bundle(path, target, manifest)
        if local is not None:
            return self._consume(self._stage_local(local), on_status)
        with _plain_errors(), self._archive_handle(cast(str, path_str), compression) as handle:
            return self._consume(self.streamingcall("stage", handle, _load_manifest(manifest)), on_status)

    def _local_bundle(  # noqa: C901 - one branch per kind of local source
        self, path: Source, target: str | None, manifest: Any
    ) -> _LocalBundle | None:
        """The files to upload for a local source, or ``None`` for an archive (sent as is)."""
        if isinstance(path, dict):
            if target is not None:
                raise click.UsageError("'target' is not valid with a target -> file mapping")
            return self._targets(path)
        if target is not None:
            if path is None:
                raise click.UsageError("'target' needs an image to flash")
            return self._targets({target: str(path)})
        if path is None:
            if manifest is None:
                raise click.UsageError("nothing to flash: pass a source, a manifest, or a stage id")
            manifest_path = Path(str(manifest))
        else:
            local = Path(path)
            if _is_url(str(path)) or not (local.is_dir() or local.suffix in (".yaml", ".yml")):
                return None  # an archive
            if manifest is not None:
                raise click.UsageError("a local bundle has its own manifest; --manifest is for archives and OCI")
            manifest_path = local / MANIFEST_NAME if local.is_dir() else local
        if not manifest_path.is_file():
            raise click.UsageError(f"{manifest_path} does not exist")
        text = manifest_path.read_text()
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise click.UsageError(f"{manifest_path} is not a FlashManifest")
        files = {ref: str((manifest_path.parent / ref).resolve()) for ref in referenced_files(data)}
        return _LocalBundle(files=files, manifest=text)

    @staticmethod
    def _targets(mapping: dict[str, str]) -> _LocalBundle:
        files: dict[str, str] = {}
        targets: list[list[str]] = []
        for name, path in mapping.items():
            bundle_path = f"{name}/{'image' if _is_url(str(path)) else Path(path).name}"
            files[bundle_path] = str(path)
            targets.append([name, bundle_path])
        return _LocalBundle(files=files, targets=targets)

    def _archive_handle(self, path: str, compression: Compression | None):
        local_path, url = _parse_path(path)
        if url is not None:
            return _http_url_adapter(client=self, url=url, mode="rb")
        assert local_path is not None
        if not local_path.is_file():
            raise click.UsageError(f"{local_path} does not exist")
        return _local_file_adapter(client=self, path=local_path, mode="rb", compression=compression)

    def _stage_local(self, bundle: _LocalBundle) -> Iterator[dict[str, Any]]:
        blobs: dict[str, str] = {}
        for index, (name, path) in enumerate(bundle.files.items(), start=1):
            yield {"phase": "download", "message": f"uploading {name}", "step_index": index,
                   "total_steps": len(bundle.files)}
            if _is_url(path):
                with _plain_errors(), _http_url_adapter(client=self, url=path, mode="rb") as handle:
                    info = self.call("stage_blob", handle, None)
            else:
                local = Path(path).resolve()
                if not local.is_file():
                    raise click.ClickException(f"{local} does not exist")
                digest = _sha256(local)
                with _plain_errors(), _local_file_adapter(client=self, path=local, mode="rb") as handle:
                    info = self.call("stage_blob", handle, digest)
            blobs[name] = info["sha256"]
        yield self.call("stage_create", blobs, bundle.manifest, bundle.targets)

    @staticmethod
    def _consume(statuses: Iterator[dict[str, Any]], on_status: StatusCallback | None) -> str:
        stage_id = None
        for status in statuses:
            if on_status:
                on_status(status)
            stage_id = status.get("stage_id", stage_id)
        if stage_id is None:
            raise RuntimeError("staging finished without a stage id")
        return stage_id

    def stages(self) -> list[dict[str, Any]]:
        return self.call("stages")

    # -- jobs --------------------------------------------------------------

    def start(self, stage_id: str, *, job_id: str | None = None, wipe: bool = False) -> JobInfo:
        """Enter fastboot, preflight, and start the exporter-side job. Idempotent on ``job_id``."""
        return self.call("start", stage_id, job_id or uuid.uuid4().hex[:12], wipe)

    def watch(self, job_id: str | None = None) -> Iterator[dict[str, Any]]:
        """Follow a job's progress (re-attachable; interrupting does not stop the job)."""
        yield from self.streamingcall("watch", job_id)

    def status(self, job_id: str | None = None) -> JobInfo:
        return self.call("status", job_id)

    def jobs(self, limit: int = 20) -> list[JobInfo]:
        return self.call("jobs", limit)

    def cancel(self, job_id: str) -> JobInfo:
        """Cancel a job that has not begun writing."""
        return self.call("cancel", job_id)

    def resume(self, job_id: str) -> JobInfo:
        """Resume an interrupted job from its first unfinished step."""
        return self.call("resume", job_id)

    def wait_exit(self, job_id: str, timeout: float | None = None) -> JobInfo:
        """Wait until a finished job's exit script (if any) has run; return the job's info."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            info = self.status(job_id)
            if info.get("exit", {}).get("state") not in ("waiting", "running"):
                return info
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"exit script of flash job {job_id} still pending after {timeout}s")
            time.sleep(1.0)

    def wait_idle(self, timeout: float | None = None) -> JobInfo | None:
        """Block until no job owns the device and its exit script has run (for scripts and CI)."""
        return self.call("wait_idle", timeout)

    # -- CLI ---------------------------------------------------------------

    def cli(self):  # noqa: C901 - one closure per command
        @driver_click_group(self)
        def base():
            """Fastboot flasher (staged on the exporter, lease-independent jobs)"""

        def source_options(fn):
            for option in reversed([
                click.argument("source", required=False),
                click.option("-t", "--target", "targets", multiple=True,
                             help="target:file, an image without a manifest (local path or URL)"),
                click.option("--manifest", help="local FlashManifest; replaces the bundle's own"),
                click.option("--compression", type=click.Choice(Compression, case_sensitive=False),
                             help="compress the upload of a local archive"),
            ]):
                fn = option(fn)
            return fn

        def resolve_source(source, targets):
            if not targets:
                return source
            if source:
                raise click.UsageError("use either SOURCE or --target, not both")
            mapping = {}
            for spec in targets:
                name, sep, path = spec.partition(":")
                if not sep or not name or not path:
                    raise click.UsageError(f"invalid --target {spec!r}, expected target:file")
                mapping[name] = path
            return mapping

        def echo(status):
            click.echo(render(status))

        def report(final, job_id):
            exit_info = final.get("exit")
            if final["phase"] == FlashPhase.ERROR:
                raise click.ClickException(f"job {job_id} {final['message']}")
            if exit_info is not None:
                click.echo(f"exit script {exit_info['state']}" +
                           (f": {exit_info['reason']}" if exit_info.get("reason") else ""))

        @base.command()
        @source_options
        @click.option("--stage", "stage_id", help="flash a bundle staged earlier")
        @click.option("--wipe", is_flag=True, help="run steps marked 'when: wipe'")
        @click.option("--detach", is_flag=True, help="start the job and return immediately")
        @click.option("--job-id", help="idempotency key for the job")
        def flash(source, targets, manifest, compression, stage_id, wipe, detach, job_id):
            """Stage, enter fastboot, preflight, and flash.

            SOURCE is oci://..., a bundle archive (path or URL), or a local bundle
            directory or manifest. Ctrl-C detaches; the flash keeps running on the
            exporter.
            """
            job_id = job_id or uuid.uuid4().hex[:12]
            src = resolve_source(source, targets)
            if detach:
                info = self.flash(src, manifest=manifest, compression=compression, stage_id=stage_id,
                                  wipe=wipe, job_id=job_id, wait=False, on_status=echo)
                click.echo(f"job {info['job_id']} started ({info['total_steps']} steps)")
                return
            final = None
            try:
                for status in self._flash_raw(src, manifest=manifest, compression=compression, target=None,
                                              stage_id=stage_id, wipe=wipe, job_id=job_id, username=None,
                                              password=None):
                    echo(status)
                    final = status
            except KeyboardInterrupt:
                click.echo(f"\ndetached: job {job_id} continues on the exporter. Reattach: j <name> watch {job_id}")
                return
            if final is not None:
                report(final, job_id)

        @base.command()
        @source_options
        def stage(source, targets, manifest, compression):
            """Copy and verify a bundle on the exporter without touching the device."""
            click.echo(self.stage(resolve_source(source, targets), manifest=manifest, compression=compression,
                                  on_status=echo))

        @base.command()
        @click.argument("job_id", required=False)
        def watch(job_id):
            """Follow a flash job (Ctrl-C detaches)."""
            job_id = job_id or self.status()["job_id"]
            try:
                for status in self.watch(job_id):
                    echo(status)
            except KeyboardInterrupt:
                click.echo(f"\ndetached: job {job_id} continues on the exporter")

        @base.command()
        @click.argument("job_id", required=False)
        def status(job_id):
            """Show a flash job's state."""
            click.echo(yaml.safe_dump(self.status(job_id), sort_keys=False).rstrip())

        @base.command()
        @click.option("--limit", default=20, show_default=True)
        def jobs(limit):
            """List recent flash jobs for this device."""
            for info in self.jobs(limit):
                click.echo(f"{info['job_id']}  {info['state']:<15} {info['steps_done']}/{info['total_steps']}  "
                           f"{info.get('source') or ''}")

        @base.command()
        def stages():
            """List bundles staged on the exporter."""
            for info in self.stages():
                click.echo(f"{info['stage_id']}  {info['bytes']:>14}  {info['source']}")

        @base.command()
        @click.argument("job_id")
        def cancel(job_id):
            """Cancel a job that has not started writing."""
            click.echo(self.cancel(job_id)["state"])

        @base.command()
        @click.argument("job_id")
        def resume(job_id):
            """Resume an interrupted job."""
            self.resume(job_id)
            for status in self.watch(job_id):
                echo(status)

        @base.command("wait-idle")
        @click.option("--timeout", type=float, default=None)
        def wait_idle(timeout):
            """Wait until no flash job owns the device and its exit script has run."""
            info = self.wait_idle(timeout)
            if info is not None:
                click.echo(f"{info['job_id']}: {info['state']}")

        @base.command()
        @click.option("--strategy", help="use only this entry strategy")
        def enter(strategy):
            """Put the device into fastboot with the exporter's entry strategies."""
            click.echo(yaml.safe_dump(self.enter(strategy), sort_keys=False).rstrip())

        @base.command("wait-present")
        @click.option("--timeout", type=float, default=0, show_default=True, help="seconds to wait")
        def wait_present(timeout):
            """Wait for the device to appear in fastboot; exit 1 if it doesn't.

            For entry scripts: hold a button until the device shows up.
            """
            if not self.wait_present(timeout):
                raise click.ClickException(f"device not present after {timeout:g}s")
            click.echo("present")

        @base.command()
        @click.argument("name")
        def getvar(name):
            """Read a bootloader variable."""
            click.echo(self.getvar(name))

        @base.command()
        @click.argument("mode", type=click.Choice(["normal", "bootloader", "fastboot"]), default="normal")
        def reboot(mode):
            """Reboot from fastboot."""
            self.reboot(mode)

        # Children as subcommands, like CompositeClient: `j <flasher> power on`.
        for name, child in self.children.items():
            if name in base.commands or isinstance(child, StubDriverClient) or not hasattr(child, "cli"):
                continue
            base.add_command(child.cli(), name)

        return base
