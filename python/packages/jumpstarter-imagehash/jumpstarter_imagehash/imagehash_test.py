import os

import imagehash
import pytest
from PIL import Image

from jumpstarter_imagehash import ImageHash


def _image_path(filename):
    current_file_directory = os.path.dirname(__file__)
    return os.path.join(current_file_directory, filename)


def test_imagehash_assert_snapshot():
    snapshot_a = SnapshotMock("test_image_a.jpeg")
    ImageHash(snapshot_a).assert_snapshot(_image_path("test_image_a.jpeg"))  # ty: ignore[invalid-argument-type]


def test_imagehash_fail_assert_snapshot(tmp_path):
    snapshot_a = SnapshotMock("test_image_a.jpeg")
    # this should raise an AssertionError
    with pytest.raises(AssertionError):
        ImageHash(snapshot_a, outdir=tmp_path).assert_snapshot(  # ty: ignore[invalid-argument-type]
            _image_path("test_image_b.jpeg")
        )
    assert (tmp_path / "FAILED_test_image_b.jpeg").exists()


def test_imagehash_passthrough_snapshot():
    snapshot_a = SnapshotMock("test_image_a.jpeg")
    assert ImageHash(snapshot_a).snapshot() == snapshot_a.img  # ty: ignore[invalid-argument-type]


def test_imagehash_hash_snapshot():
    snapshot_a = SnapshotMock("test_image_a.jpeg")
    assert isinstance(ImageHash(snapshot_a).hash_snapshot(), imagehash.ImageHash)  # ty: ignore[invalid-argument-type]


class SnapshotMock:
    def __init__(self, filename):
        self.name = "SnapshotMock"
        self.img = Image.open(_image_path(filename))

    def snapshot(self):
        return self.img
