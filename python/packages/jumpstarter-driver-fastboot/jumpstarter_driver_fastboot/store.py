"""Exporter-side staging: every blob is copied to and verified on the exporter
before the device is touched.

Layout under ``state_dir``::

    blobs/sha256/<hex>          decompressed, verified content (content-addressed)
    layers/sha256/<hex>.json    OCI layer digest -> processed blob (pull cache)
    stages/<stage_id>/stage.json  immutable: source, manifest, file -> blob map

Unlike the RideSX Opendal storage, nothing here is deleted at lease end.
Unreferenced stages are evicted LRU against a byte cap; stages referenced by an
unfinished job are never evicted.
"""

from __future__ import annotations

import bz2
import gzip
import hashlib
import json
import lzma
import os
import posixpath
import shutil
import struct
import tarfile
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CHUNK = 1024 * 1024
SPARSE_MAGIC = 0xED26FF3A

Progress = Callable[[dict[str, Any]], None]


class StageError(RuntimeError):
    """Staging failed; the device has not been touched."""


@dataclass(frozen=True)
class BlobInfo:
    sha256: str
    path: str
    size: int
    sparse: bool
    expanded_size: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BlobInfo:
        return cls(**data)


def sparse_expanded_size(path: Path) -> int | None:
    """Return the expanded size of an Android sparse image, or ``None`` if not sparse.

    Raises ``StageError`` for a sparse image with an invalid header.
    """
    with open(path, "rb") as f:
        header = f.read(28)
    if len(header) < 28 or struct.unpack_from("<I", header, 0)[0] != SPARSE_MAGIC:
        return None
    major, _minor, file_hdr_sz, chunk_hdr_sz, blk_sz, total_blks, _chunks, _crc = struct.unpack_from(
        "<HHHHIIII", header, 4
    )
    if major != 1 or file_hdr_sz < 28 or chunk_hdr_sz < 12 or blk_sz == 0 or blk_sz % 4:
        raise StageError(f"{path.name}: invalid Android sparse image header")
    return blk_sz * total_blks


def _compression(path: Path) -> str | None:
    with open(path, "rb") as f:
        magic = f.read(6)
    if magic[:2] == b"\x1f\x8b":
        return "gzip"
    if magic == b"\xfd7zXZ\x00":
        return "xz"
    if magic[:4] == b"\x28\xb5\x2f\xfd":
        return "zstd"
    if magic[:3] == b"BZh":
        return "bz2"
    return None


def _open_decompressor(kind: str, f):
    if kind == "gzip":
        return gzip.GzipFile(fileobj=f)
    if kind == "xz":
        return lzma.LZMAFile(f)
    if kind == "bz2":
        return bz2.BZ2File(f)
    if kind == "zstd":
        import zstandard

        return zstandard.ZstdDecompressor().stream_reader(f)
    raise ValueError(kind)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


class StageStore:
    def __init__(self, root: str | Path, *, reserve_bytes: int = 1 << 30):
        self.root = Path(root)
        self.reserve_bytes = reserve_bytes
        for sub in ("blobs/sha256", "layers/sha256", "stages", "tmp"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # -- space ---------------------------------------------------------------

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root).free

    def ensure_space(self, needed: int) -> None:
        free = self.free_bytes()
        if free < needed + self.reserve_bytes:
            raise StageError(
                f"not enough space in {self.root}: need {needed} bytes plus {self.reserve_bytes} reserve, "
                f"{free} free"
            )

    # -- blobs ---------------------------------------------------------------

    def tmpfile(self) -> Path:
        fd, name = tempfile.mkstemp(dir=self.root / "tmp", prefix="in-")
        os.close(fd)
        return Path(name)

    def ingest(self, path: Path, *, expected_sha256: str | None = None) -> BlobInfo:
        """Verify, decompress, and move a downloaded file into the content store.

        ``expected_sha256`` is checked against the bytes as received (before
        decompression). The file at ``path`` is consumed.
        """
        try:
            if expected_sha256 is not None:
                actual = sha256_file(path)
                if actual != expected_sha256.removeprefix("sha256:"):
                    raise StageError(f"digest mismatch: expected {expected_sha256}, got sha256:{actual}")

            kind = _compression(path)
            if kind is not None:
                out = self.tmpfile()
                try:
                    with open(path, "rb") as raw, _open_decompressor(kind, raw) as src, open(out, "wb") as dst:
                        written = 0
                        while chunk := src.read(CHUNK):
                            dst.write(chunk)
                            written += len(chunk)
                            if written % (256 * CHUNK) < len(chunk) and self.free_bytes() < self.reserve_bytes:
                                raise StageError(f"ran out of space decompressing into {self.root}")
                        dst.flush()
                        os.fsync(dst.fileno())
                except (OSError, EOFError, lzma.LZMAError) as exc:
                    out.unlink(missing_ok=True)
                    raise StageError(f"failed to decompress {kind} content: {exc}") from exc
                except BaseException:
                    out.unlink(missing_ok=True)
                    raise
                path.unlink(missing_ok=True)
                path = out

            digest = sha256_file(path)
            expanded = sparse_expanded_size(path)
            size = path.stat().st_size
            final = self.root / "blobs" / "sha256" / digest
            if final.exists():
                path.unlink(missing_ok=True)
            else:
                os.chmod(path, 0o444)
                os.replace(path, final)
            return BlobInfo(
                sha256=digest,
                path=str(final),
                size=size,
                sparse=expanded is not None,
                expanded_size=expanded if expanded is not None else size,
            )
        finally:
            if path.exists() and path.parent == self.root / "tmp":
                path.unlink(missing_ok=True)

    def blob(self, sha256: str) -> BlobInfo:
        """Describe a stored blob by digest."""
        digest = sha256.removeprefix("sha256:")
        if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
            raise StageError(f"invalid digest {sha256!r}")
        path = self.root / "blobs" / "sha256" / digest
        if not path.exists():
            raise StageError(f"blob {digest} is not staged on this exporter")
        expanded = sparse_expanded_size(path)
        size = path.stat().st_size
        return BlobInfo(sha256=digest, path=str(path), size=size, sparse=expanded is not None,
                        expanded_size=expanded if expanded is not None else size)

    def layer_cache_get(self, oci_digest: str) -> BlobInfo | None:
        entry = self.root / "layers" / "sha256" / f"{oci_digest.removeprefix('sha256:')}.json"
        if not entry.exists():
            return None
        info = BlobInfo.from_dict(json.loads(entry.read_text()))
        if not Path(info.path).exists():
            entry.unlink(missing_ok=True)
            return None
        return info

    def layer_cache_put(self, oci_digest: str, info: BlobInfo) -> None:
        entry = self.root / "layers" / "sha256" / f"{oci_digest.removeprefix('sha256:')}.json"
        _atomic_write(entry, json.dumps(asdict(info)))

    # -- stages --------------------------------------------------------------

    def create_stage(
        self,
        *,
        source: str,
        manifest: dict[str, Any],
        files: dict[str, BlobInfo],
        synthesized: bool,
    ) -> str:
        stage_id = uuid.uuid4().hex[:16]
        stage_dir = self.root / "stages" / stage_id
        stage_dir.mkdir(parents=True)
        payload = {
            "stage_id": stage_id,
            "source": source,
            "created": time.time(),
            "synthesized": synthesized,
            "manifest": manifest,
            "files": {name: asdict(info) for name, info in files.items()},
        }
        _atomic_write(stage_dir / "stage.json", json.dumps(payload, indent=2))
        return stage_id

    def load_stage(self, stage_id: str) -> dict[str, Any]:
        if not stage_id or "/" in stage_id or stage_id.startswith("."):
            raise StageError(f"invalid stage id {stage_id!r}")
        path = self.root / "stages" / stage_id / "stage.json"
        if not path.exists():
            raise StageError(f"unknown stage {stage_id!r}")
        stage = json.loads(path.read_text())
        for name, info in stage["files"].items():
            if not Path(info["path"]).exists():
                raise StageError(f"stage {stage_id} is incomplete: blob for {name} was evicted")
        os.utime(path)  # LRU
        return stage

    def list_stages(self) -> list[dict[str, Any]]:
        out = []
        for path in sorted((self.root / "stages").glob("*/stage.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            stage = json.loads(path.read_text())
            out.append(
                {
                    "stage_id": stage["stage_id"],
                    "source": stage["source"],
                    "created": stage["created"],
                    "files": len(stage["files"]),
                    "bytes": sum(f["size"] for f in stage["files"].values()),
                }
            )
        return out

    def gc(self, cap_bytes: int, pinned: set[str]) -> None:
        """Evict least-recently-used unpinned stages, then unreferenced blobs."""
        stages = sorted((self.root / "stages").glob("*/stage.json"), key=lambda p: p.stat().st_mtime)
        refs: dict[str, set[str]] = {}
        for path in stages:
            stage = json.loads(path.read_text())
            refs[stage["stage_id"]] = {f["path"] for f in stage["files"].values()}

        def used() -> int:
            blobs = set().union(*refs.values()) if refs else set()
            return sum(Path(b).stat().st_size for b in blobs if Path(b).exists())

        for path in stages:
            if used() <= cap_bytes:
                break
            stage_id = path.parent.name
            if stage_id in pinned:
                continue
            shutil.rmtree(path.parent, ignore_errors=True)
            refs.pop(stage_id, None)

        live = set().union(*refs.values()) if refs else set()
        for blob in (self.root / "blobs" / "sha256").iterdir():
            if str(blob) not in live:
                blob.unlink(missing_ok=True)
        for entry in (self.root / "layers" / "sha256").glob("*.json"):
            info = json.loads(entry.read_text())
            if info["path"] not in live:
                entry.unlink(missing_ok=True)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# -- archives ----------------------------------------------------------------


def ingest_archive(store: StageStore, path: Path, *, progress: Progress) -> dict[str, BlobInfo]:
    """Unpack a tar bundle (plain, or gzip/xz/bzip2/zstd compressed) into the store.

    Every regular file becomes a verified, content-addressed blob, named by its
    path in the archive. Members that would escape the bundle are rejected;
    directories, links, and device nodes are skipped. The archive is read as a
    stream, so it is never unpacked to disk as a whole.
    """
    kind = _compression(path)
    files: dict[str, BlobInfo] = {}
    with open(path, "rb") as raw:
        src = _open_decompressor(kind, raw) if kind is not None else raw
        try:
            with tarfile.open(fileobj=src, mode="r|") as tar:
                for member in tar:
                    if not member.isfile():
                        continue
                    name = posixpath.normpath(member.name)
                    if name.startswith(("/", "../")) or name in ("..", "."):
                        raise StageError(f"archive member {member.name!r} escapes the bundle")
                    progress({"phase": "extract", "message": f"extracting {name}", "bytes_total": member.size})
                    extracted = tar.extractfile(member)
                    if extracted is None:
                        continue
                    tmp = store.tmpfile()
                    try:
                        with open(tmp, "wb") as out:
                            written = 0
                            while chunk := extracted.read(CHUNK):
                                out.write(chunk)
                                written += len(chunk)
                                if written % (256 * CHUNK) < len(chunk) and store.free_bytes() < store.reserve_bytes:
                                    raise StageError(f"ran out of space extracting into {store.root}")
                            out.flush()
                            os.fsync(out.fileno())
                        files[name] = store.ingest(tmp)
                    finally:
                        tmp.unlink(missing_ok=True)
        except (tarfile.TarError, EOFError, lzma.LZMAError) as exc:
            raise StageError(
                f"not a bundle archive (a tar file, optionally gzip/xz/bzip2/zstd compressed): {exc}"
            ) from exc
        finally:
            if src is not raw:
                src.close()
    if not files:
        raise StageError("the archive contains no files")
    return files


def bundle_root(files: dict[str, BlobInfo], manifest_name: str) -> tuple[str, str | None]:
    """Where an archive's manifest is: ``(prefix, manifest file)``.

    The manifest is ``manifest_name`` at the root of the archive, or inside a
    single top-level directory (``firmware-1.2/manifest.yaml``); file references
    are relative to it. ``("", None)`` when the archive has no manifest.
    """
    if manifest_name in files:
        return "", manifest_name
    nested = sorted(name for name in files if name.count("/") == 1 and name.endswith("/" + manifest_name))
    if len(nested) == 1:
        return nested[0].rsplit("/", 1)[0] + "/", nested[0]
    if len(nested) > 1:
        raise StageError(f"the archive has several manifests: {', '.join(nested)}")
    return "", None


# -- OCI ---------------------------------------------------------------------

TITLE = "org.opencontainers.image.title"
# Annotation-only bundles (``fls`` style): each layer names its target, the manifest gives the order.
TARGET_KEYS = ("dev.jumpstarter.fls/partition", "automotive.sdv.cloud.redhat.com/partition")
DEFAULT_TARGETS_KEYS = (
    "dev.jumpstarter.fls/default-partitions",
    "automotive.sdv.cloud.redhat.com/default-partitions",
)


@dataclass
class PulledBundle:
    files: dict[str, BlobInfo]
    manifest_text: str | None
    targets: list[tuple[str, str]]  # (target, file) from layer annotations, in flash order


def pull_oci(  # noqa: C901 - layer loop with cache, manifest, and annotation handling
    store: StageStore,
    reference: str,
    *,
    manifest_name: str,
    username: str | None,
    password: str | None,
    insecure: bool,
    progress: Progress,
) -> PulledBundle:
    """Pull an OCI bundle into the store, verifying every layer digest.

    The OCI manifest is always fetched with the caller's credentials, so a
    cached layer is only reused after the caller has proven access to it.
    """
    from oras.provider import Registry

    registry = Registry(insecure=insecure)
    if username and password:
        registry.auth.set_basic_auth(username, password)
    container = registry.get_container(reference)
    try:
        oci_manifest = registry.get_manifest(container)
    except Exception as exc:
        raise StageError(f"cannot read OCI manifest for {reference}: {exc}") from exc

    layers = oci_manifest.get("layers", [])
    if not layers:
        raise StageError(f"{reference} has no layers")

    todo = [layer for layer in layers if store.layer_cache_get(layer["digest"]) is None]
    store.ensure_space(sum(int(layer.get("size", 0)) for layer in todo))

    files: dict[str, BlobInfo] = {}
    annotated: dict[str, str] = {}
    manifest_text = None
    total = len(layers)
    for index, layer in enumerate(layers, start=1):
        annotations = layer.get("annotations") or {}
        digest = layer["digest"]
        title = annotations.get(TITLE) or digest.replace(":", "-")
        name = os.path.normpath(title).lstrip("/")
        if annotations.get("io.deis.oras.content.unpack") == "true":
            raise StageError(f"layer {title!r} is a directory archive; push files individually")

        info = store.layer_cache_get(digest)
        if info is not None:
            progress({"phase": "cache", "message": f"cached {name}", "step_index": index, "total_steps": total})
        else:
            progress({"phase": "download", "message": f"pulling {name}", "step_index": index, "total_steps": total,
                      "bytes_total": layer.get("size")})
            tmp = store.tmpfile()
            try:
                _download_blob(registry, container, digest, tmp)  # verifies the layer digest
                info = store.ingest(tmp)
            finally:
                tmp.unlink(missing_ok=True)
            store.layer_cache_put(digest, info)
        files[name] = info

        if name == manifest_name:
            manifest_text = Path(info.path).read_text()
        for key in TARGET_KEYS:
            if key in annotations:
                annotated[annotations[key]] = name
                break

    targets: list[tuple[str, str]] = []
    if annotated:
        top = oci_manifest.get("annotations") or {}
        order = next((top[k] for k in DEFAULT_TARGETS_KEYS if k in top), None)
        names = [p.strip() for p in order.split(",") if p.strip()] if order else sorted(annotated)
        targets = [(p, annotated[p]) for p in names if p in annotated]
    return PulledBundle(files=files, manifest_text=manifest_text, targets=targets)


def _download_blob(registry, container, digest: str, outfile: Path) -> None:
    algorithm, _, expected = digest.partition(":")
    if algorithm not in ("sha256", "sha512"):
        raise StageError(f"layer {digest}: unsupported digest algorithm")
    hasher = hashlib.new(algorithm)
    with registry.get_blob(container, digest, stream=True) as response:
        response.raise_for_status()
        with open(outfile, "wb") as f:
            for chunk in response.iter_content(chunk_size=CHUNK):
                if chunk:
                    f.write(chunk)
                    hasher.update(chunk)
            f.flush()
            os.fsync(f.fileno())
    if hasher.hexdigest() != expected:
        raise StageError(f"layer {digest} failed digest verification")
