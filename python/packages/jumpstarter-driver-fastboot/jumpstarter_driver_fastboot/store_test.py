import gzip
import hashlib
import lzma
import struct
from pathlib import Path

import pytest
import zstandard

from .store import SPARSE_MAGIC, StageError, StageStore, bundle_root, ingest_archive, pull_oci


def sparse_image(blocks: int = 16, block_size: int = 4096) -> bytes:
    header = struct.pack("<IHHHHIIII", SPARSE_MAGIC, 1, 0, 28, 12, block_size, blocks, 1, 0)
    return header + b"\0" * 64


@pytest.fixture
def store(tmp_path):
    return StageStore(tmp_path / "state", reserve_bytes=0)


def write(store, data: bytes):
    path = store.tmpfile()
    path.write_bytes(data)
    return path


@pytest.mark.parametrize(
    "compress",
    [lambda d: d, gzip.compress, lzma.compress, lambda d: zstandard.ZstdCompressor().compress(d)],
)
def test_ingest_decompresses_and_content_addresses(store, compress):
    payload = b"boot image " * 1000
    raw = compress(payload)
    info = store.ingest(write(store, raw), expected_sha256=hashlib.sha256(raw).hexdigest())
    assert info.sha256 == hashlib.sha256(payload).hexdigest()
    assert Path(info.path).read_bytes() == payload
    assert not list((store.root / "tmp").iterdir())


def test_ingest_rejects_digest_mismatch(store):
    path = write(store, b"tampered")
    with pytest.raises(StageError, match="digest mismatch"):
        store.ingest(path, expected_sha256="0" * 64)
    assert not list((store.root / "blobs" / "sha256").iterdir())


def test_sparse_images_report_expanded_size(store):
    info = store.ingest(write(store, sparse_image(blocks=256)))
    assert info.sparse and info.expanded_size == 256 * 4096


def test_invalid_sparse_header_rejected(store):
    bad = struct.pack("<IHHHHIIII", SPARSE_MAGIC, 2, 0, 28, 12, 4096, 1, 1, 0)
    with pytest.raises(StageError, match="sparse"):
        store.ingest(write(store, bad))


def test_space_check(store):
    store.reserve_bytes = 1 << 62
    with pytest.raises(StageError, match="not enough space"):
        store.ensure_space(1)


def test_gc_keeps_pinned_stages(store):
    a = store.ingest(write(store, b"a" * 1000))
    b = store.ingest(write(store, b"b" * 1000))
    old = store.create_stage(source="old", manifest={}, files={"a": a}, synthesized=True)
    pinned = store.create_stage(source="pinned", manifest={}, files={"b": b}, synthesized=True)
    store.gc(0, pinned={pinned})
    assert not (store.root / "stages" / old).exists()
    assert store.load_stage(pinned)["source"] == "pinned"
    with pytest.raises(StageError):
        store.load_stage(old)


# -- OCI (oras-py Registry replaced by an in-memory fake) -----------------------


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        for i in range(0, len(self.data), chunk_size):
            yield self.data[i : i + chunk_size]


def fake_registry(monkeypatch, layers, annotations=None, tamper=None):
    blobs = {}
    manifest_layers = []
    for title, data, extra in layers:
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        blobs[digest] = tamper(data) if tamper else data
        manifest_layers.append(
            {"digest": digest, "size": len(data), "annotations": {"org.opencontainers.image.title": title} | extra}
        )
    calls = {"auth": None, "blobs": 0}

    class Registry:
        def __init__(self, insecure=False):
            self.auth = self

        def set_basic_auth(self, user, password):
            calls["auth"] = (user, password)

        def get_container(self, ref):
            return ref

        def get_manifest(self, container):
            return {"layers": manifest_layers, "annotations": annotations or {}}

        def get_blob(self, container, digest, stream=False):
            calls["blobs"] += 1
            return FakeResponse(blobs[digest])

    import oras.provider

    monkeypatch.setattr(oras.provider, "Registry", Registry)
    return calls


def test_pull_oci_bundle_with_manifest(monkeypatch, store):
    calls = fake_registry(
        monkeypatch,
        [("manifest.yaml", b"kind: FlashManifest\n", {}), ("./data/boot.img", gzip.compress(b"x" * 4096), {})],
    )
    pulled = pull_oci(store, "quay.io/org/img:1", manifest_name="manifest.yaml", username="u", password="p",
                      insecure=False, progress=lambda _: None)
    assert pulled.manifest_text is not None
    assert pulled.manifest_text.startswith("kind: FlashManifest")
    assert Path(pulled.files["data/boot.img"].path).read_bytes() == b"x" * 4096  # decompressed
    assert calls["auth"] == ("u", "p")

    # A second pull re-reads the manifest (re-authorizes) but reuses verified layers.
    pull_oci(store, "quay.io/org/img:1", manifest_name="manifest.yaml", username=None, password=None,
             insecure=False, progress=lambda _: None)
    assert calls["blobs"] == 2


def test_pull_oci_annotation_order(monkeypatch, store):
    fake_registry(
        monkeypatch,
        [
            ("system.simg", b"s" * 100, {"automotive.sdv.cloud.redhat.com/partition": "system_a"}),
            ("boot.img", b"b" * 100, {"automotive.sdv.cloud.redhat.com/partition": "boot_a"}),
            ("vbmeta.img", b"v" * 100, {"automotive.sdv.cloud.redhat.com/partition": "vbmeta_a"}),
        ],
        annotations={"automotive.sdv.cloud.redhat.com/default-partitions": "boot_a,system_a"},
    )
    pulled = pull_oci(store, "r/x:1", manifest_name="manifest.yaml", username=None, password=None,
                      insecure=False, progress=lambda _: None)
    assert pulled.manifest_text is None
    assert pulled.targets == [("boot_a", "boot.img"), ("system_a", "system.simg")]


def test_pull_oci_rejects_tampered_layer(monkeypatch, store):
    fake_registry(monkeypatch, [("boot.img", b"good" * 100, {})], tamper=lambda d: b"evil" * 100)
    with pytest.raises(StageError, match="digest verification"):
        pull_oci(store, "r/x:1", manifest_name="manifest.yaml", username=None, password=None,
                 insecure=False, progress=lambda _: None)


def tar_bytes(members: dict[str, bytes], compress=lambda d: d) -> bytes:
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return compress(buf.getvalue())


@pytest.mark.parametrize("compress", [lambda d: d, gzip.compress, lzma.compress,
                                      lambda d: zstandard.ZstdCompressor().compress(d)])
def test_ingest_archive_unpacks_every_file(store, compress):
    data = tar_bytes({"fw-1.0/manifest.yaml": b"kind: FlashManifest\n", "fw-1.0/data/boot.img": gzip.compress(b"boot")},
                     compress)
    seen = []
    files = ingest_archive(store, write(store, data), progress=seen.append)
    assert set(files) == {"fw-1.0/manifest.yaml", "fw-1.0/data/boot.img"}
    assert Path(files["fw-1.0/data/boot.img"].path).read_bytes() == b"boot"  # members are decompressed too
    assert [s["phase"] for s in seen] == ["extract", "extract"]
    assert bundle_root(files, "manifest.yaml") == ("fw-1.0/", "fw-1.0/manifest.yaml")


def test_ingest_archive_rejects_escaping_members(store):
    with pytest.raises(StageError, match="escapes the bundle"):
        ingest_archive(store, write(store, tar_bytes({"../evil": b"x"})), progress=lambda _: None)


def test_ingest_archive_rejects_non_archives(store):
    with pytest.raises(StageError, match="not a bundle archive"):
        ingest_archive(store, write(store, b"just an image" * 100), progress=lambda _: None)


def test_bundle_root_at_top_level_or_absent(store):
    files = ingest_archive(store, write(store, tar_bytes({"manifest.yaml": b"x", "a/b": b"y"})), progress=print)
    assert bundle_root(files, "manifest.yaml") == ("", "manifest.yaml")
    assert bundle_root(files, "other.yaml") == ("", None)
