"""Keywords for images from video drivers.

`Capture Video Snapshot` saves a frame from a video source (``VideoClient``
and ``UStreamerClient`` from ``jumpstarter-driver-video``, or any client with
``snapshot_bytes()``) under the Robot output directory and shows it in the
log. Video nodes also get a node-prefixed ``<Prefix> Capture Snapshot``
keyword in their ``Driver`` library.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from robot.api import Failure, logger
from robot.api.deco import keyword

from .. import context, sessions
from ..errors import mapped_errors
from ..extensions import register_node_extension
from .contexts import resolve
from jumpstarter.client import DriverClient

# Client classes whose snapshots this module knows, matched by MRO class name.
# NanoKVMUSBVideoClient has only ``snapshot()``, returning a PIL image.
SNAPSHOT_CLIENTS = frozenset({"VideoClient", "NanoKVMUSBVideoClient"})

_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)


def image_suffix(data: bytes) -> str:
    """The file suffix for encoded image ``data``; ``.jpg`` (what video drivers send) when unknown."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    for signature, suffix in _SIGNATURES:
        if data.startswith(signature):
            return suffix
    return ".jpg"


def _frame(node: DriverClient, path: str) -> bytes | Any:
    """Encoded bytes from ``snapshot_bytes()``, else what ``snapshot()`` returns (bytes or a PIL image)."""
    snapshot_bytes = getattr(node, "snapshot_bytes", None)
    if callable(snapshot_bytes):
        return snapshot_bytes()
    snapshot = getattr(node, "snapshot", None)
    if callable(snapshot):
        return snapshot()
    raise Failure(f"driver client {type(node).__name__} at '{path}' has no snapshot_bytes() or snapshot()")


def _destination(filename: str | None, path: str, suffix: str) -> Path:
    if not filename:
        return context.artifact_path(f"{path or 'root'}-snapshot", suffix)
    destination = Path(filename)
    if not destination.is_absolute():
        destination = context.output_dir() / destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def _save(frame: bytes | Any, filename: str | None, path: str) -> Path:
    if isinstance(frame, (bytes, bytearray, memoryview)):
        data = bytes(frame)
        if not data:
            raise Failure(f"the video driver at '{path}' returned an empty snapshot")
        destination = _destination(filename, path, image_suffix(data))
        destination.write_bytes(data)
        return destination
    if callable(getattr(frame, "save", None)):  # a PIL image
        destination = _destination(filename, path, ".png")
        frame.save(destination)
        return destination
    raise Failure(f"the video driver at '{path}' returned {type(frame).__name__}, not an image")


def capture_snapshot(path: str, session: str | None, filename: str | None = None, embed: bool = True) -> str:
    """Save one frame from the video driver at ``path``; returns the file path."""
    with sessions.lock(), mapped_errors("Capture Video Snapshot"):
        _, node = resolve(path, session)
        destination = _save(_frame(node, path), filename, path)
    if embed:
        context.embed_image(destination)
    else:
        logger.info(f"saved snapshot to {destination}")
    return str(destination)


def _capture_node_snapshot(path: str, session: str | None, filename: str | None = None, embed: bool = True) -> str:
    """Save one frame from this video driver and return the file path.

    The image goes to ``filename`` (relative to the ``jumpstarter`` folder
    in the output directory), or to a new numbered file there, and is shown
    in the log unless ``embed`` is false.

    | ${image}= | Camera Capture Snapshot |
    | Camera Capture Snapshot | boot-screen.jpg | embed=False |
    """
    return capture_snapshot(path, session, filename, embed)


register_node_extension(SNAPSHOT_CLIENTS, "Capture Snapshot", _capture_node_snapshot, tags=("media",))


class MediaKeywords:
    """Snapshots from video drivers."""

    @keyword
    def capture_video_snapshot(
        self, path: str, filename: str | None = None, embed: bool = True, session: str | None = None
    ) -> str:
        """Save one frame from the video driver at ``path`` and return the file path.

        Uses the client's ``snapshot_bytes()`` (``VideoClient``,
        ``UStreamerClient``) and keeps the encoded image as the driver sent
        it, usually JPEG; clients with only ``snapshot()`` returning an image
        are saved as PNG. The file is ``filename`` (relative to the
        ``jumpstarter`` folder in the output directory unless absolute), or a
        new numbered file there. The image is shown in the log unless
        ``embed`` is false.

        | ${image}= | Capture Video Snapshot | dut.video |
        | Capture Video Snapshot | hdmi | filename=boot-screen.jpg | embed=False |
        """
        return capture_snapshot(path, session, filename, embed)
