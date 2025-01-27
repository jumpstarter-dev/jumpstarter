import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import imagehash
from PIL import Image

from jumpstarter.client import DriverClient

log = logging.getLogger("imagehash")


@dataclass
class ImageHash:
    """ImageHash class

    ImageHash class for image hashing and comparison

    param client: video client object, must provide a snapshot method
    type client: object
    param outdir: where to store actual image files, default is Path.cwd()
    type outdir: Path
    param hash_func: hash function to use from imagehash library, default is average_hash
    type hash_func: function
    param hash_size: hash size to use from imagehash library, default is 8
    type hash_size: int
    """

    client: DriverClient
    outdir: Path = field(default_factory=Path.cwd)
    hash_func: Any = imagehash.average_hash
    hash_size: int = 8

    def snapshot(self):
        """
        Get a snapshot image from the video input

        :return: a snapshot image
        :rtype: PIL.Image
        """
        return self.client.snapshot()

    def hash_snapshot(self):
        """
        Get a hash of the snapshot image through the imagehash library

        :return: hash of the snapshot image
        :rtype: ImageHash
        """
        return self.hash_func(self.snapshot())

    def assert_snapshot(self, reference_img_file, tolerance=1):
        """
        Assert the snapshot image is the same as the reference image

        :param str reference_img_file: reference image file name
        :param int tolerance: hash difference tolerance

        :raises AssertionError: if the snapshot image is different from the reference image
        """
        diff, snapshot_img = self._snapshot_diff(reference_img_file)
        if diff > tolerance:
            reference_img_file = "FAILED_" + os.path.basename(reference_img_file)

            save_filename = self.outdir / reference_img_file
            log.error(f"Image hashes are different, saving the actual image as {save_filename}")
            snapshot_img.save(save_filename)
            raise AssertionError(
                f"{self.client.name}.assert_snapshot {reference_img_file}: diff {diff} > tolerance {tolerance}"
            )

    def _snapshot_diff(self, reference_img_file, hash_func=imagehash.average_hash, hash_size=8):
        snapshot_img = self.snapshot()
        ref_hash = self.hash_func(Image.open(reference_img_file), hash_size=self.hash_size)
        snapshot_hash = self.hash_func(snapshot_img, hash_size=self.hash_size)
        diff = ref_hash - snapshot_hash
        log.info(
            f"{self.client.name} comparing snapshot {reference_img_file}:"
            f" snapshot {snapshot_hash}, ref {ref_hash}, diff: {diff}"
        )
        return diff, snapshot_img
