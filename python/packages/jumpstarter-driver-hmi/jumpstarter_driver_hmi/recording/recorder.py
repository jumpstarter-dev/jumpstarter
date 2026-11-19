"""Record the stream to a fragmented MP4 file on the exporter: no transcode, no ffmpeg, nothing on the device.

A :class:`Recording` is one more viewer of the :class:`~jumpstarter_driver_hmi.hub.Hub`: it joins at the next key
picture like any viewer, reads the same transport frames (``u32 length + u8 kind + body``, see
:mod:`~jumpstarter_driver_hmi.common`) and gives the stream's own H.264 or HEVC access units, as they are, to
:class:`~jumpstarter_driver_hmi.recording.mp4.Fmp4Muxer`. A file that is cut off (a crash, a full disk) plays up to
the last whole fragment.

Which codecs
------------

The file holds one codec, the family (``h264`` or ``hevc``) of the codec line in force when its first picture arrived.
**A Motion JPEG stream is not recorded**: :meth:`Recording.start` refuses it with a ``RuntimeError``. (A recording
that began before the stream said its codec finds out when the first picture arrives, and ends with that message as its
``error``.) A stream that changes its **family** while being recorded (the codec of the HMI was changed, so the
pictures are of another kind) ends the recording cleanly: the file holds what came before, and the reason is
``codec_changed``. Any other change (the picture size, the rotation, a new SPS of the same codec) goes on in the same
file, as the muxer describes.

Threads and the viewer's lag policy
-----------------------------------

The hub sheds a viewer's queued pictures when its backlog passes ``max_lag_s`` (0.6 s) or ``max_queue_bytes``
(non-reference pictures first; then, far behind, everything up to the next key picture: a gap in the video). A recording
must not get gaps because the disk stalled for a second, so it never lets its viewer queue grow: the viewer's ``notify``
callback (called by the reader thread right after it queued a frame) **pops the frame at once** into the recording's own
list, which holds bytes in memory only; a separate writer thread muxes and writes from that list. The viewer's queue
therefore stays empty, whatever the disk does, and its lag policy never applies. The recording's own list is bounded
(``max_pending_bytes``, 64 MB by default: about two minutes of a 4 Mbit/s stream): if the disk is that far behind, the
recording ends with ``error`` set and the file is closed cleanly, rather than the exporter's memory growing. The hub
needs no special case for a recording.

Joining: a viewer is served the hub's cached GOP at once (the last IDR and the pictures since), so a recording begins at
that key picture, which is up to ``idr_interval_s`` of video from *before* the call; ``max_seconds`` counts that video
too. A hub with no GOP to give makes the recording wait for the next key picture.

Where it writes
---------------

``Recording.start(hub, path, root=...)``: with ``root`` the file must be inside that directory (a relative ``path`` is
taken from it; ``..`` and a symlinked directory that leads out are refused); the file is created new (``O_EXCL``: an
existing file or a symlink at that name is refused) with mode ``0600``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Self

from ..common import DEFAULT_CODEC, KIND_CONFIG, KIND_MEDIA, FrameParser, codec_family, split_media
from .mp4 import Fmp4Muxer

if TYPE_CHECKING:
    from ..hub.hub import Hub

log = logging.getLogger(__name__)

_MJPEG_NOT_RECORDED = (
    "an MJPEG stream is not recorded (a recording is an MP4 of H.264 or HEVC video): "
    "use the h264 or hevc codec to record the HMI"
)

#: Why a recording ended (``reason`` of the result).
REASON_STOP = "stop"
REASON_MAX_SECONDS = "max_seconds"
REASON_MAX_BYTES = "max_bytes"
REASON_STREAM_ENDED = "stream_ended"
REASON_CODEC_CHANGED = "codec_changed"
REASON_ERROR = "error"

#: A stream that goes quiet is cut off this many seconds after ``max_seconds`` (a still screen sends no pictures).
_WALL_GRACE_S = 1.0
_JOIN_TIMEOUT_S = 15.0


class Recording:
    """One running (or finished) recording. Make it with :meth:`start`; end it with :meth:`stop`."""

    def __init__(
        self,
        hub: Hub,
        path: Path,
        file: BinaryIO,
        *,
        max_seconds: float | None,
        max_bytes: int | None,
        fragment_ms: int,
        max_pending_bytes: int,
    ) -> None:
        """Use :meth:`start`; this takes a hub, the (already created) file and the limits."""
        self._hub = hub
        self._path = path
        self._file = file
        self._max_us = None if max_seconds is None else int(max_seconds * 1_000_000)
        self._max_seconds = max_seconds
        self._max_bytes = max_bytes
        self._fragment_ms = fragment_ms
        self._max_pending = max_pending_bytes
        self._cv = threading.Condition()
        self._pending: deque[Any] = deque()
        self._pending_bytes = 0
        self._stop = False
        self._overflow = False
        self._viewer: Any = None
        self._parser = FrameParser()
        self._muxer: Fmp4Muxer | None = None
        self._line: dict[str, Any] = {}
        self._written = 0
        self._io_failed = False
        self._deadline: float | None = None
        self._reason: str | None = None
        self._error: str | None = None
        self._result: dict[str, Any] | None = None
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"hmi-record-{path.name}", daemon=True)

    # ---- starting ------------------------------------------------------------------------------------------------

    @classmethod
    def start(
        cls,
        hub: Hub,
        path: str | os.PathLike[str],
        *,
        max_seconds: float | None = None,
        max_bytes: int | None = None,
        root: str | os.PathLike[str] | None = None,
        fragment_ms: int = 500,
        max_pending_bytes: int = 64_000_000,
    ) -> Recording:
        """Start recording ``hub`` to ``path``; returns at once (the first picture comes at the next key picture).

        ``max_seconds`` ends the recording once that much video is in the file (it can hold one picture more);
        ``max_bytes`` once the file has reached that size (it can exceed it by up to a fragment, ``fragment_ms`` of
        video). ``root``: see the module docstring.

        Raises:
            ValueError: If a limit is not positive, or ``path`` is not a plain file name inside ``root``.
            FileNotFoundError: If the directory does not exist.
            FileExistsError: If the file already exists.
            jumpstarter_driver_hmi.hub.StreamEnded: If the hub has ended (the file is removed again).
            OSError: If the file cannot be created.
            RuntimeError: If the stream is Motion JPEG, which is not recorded (no file is made).
        """
        if max_seconds is not None and not max_seconds > 0:
            raise ValueError("max_seconds must be positive")
        if max_bytes is not None and max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        target = _checked_path(path, root)
        line = hub.line
        if line is not None and line.family == "mjpeg":
            raise RuntimeError(_MJPEG_NOT_RECORDED)
        fd = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        recording = cls(
            hub,
            target,
            os.fdopen(fd, "wb"),
            max_seconds=max_seconds,
            max_bytes=max_bytes,
            fragment_ms=fragment_ms,
            max_pending_bytes=max_pending_bytes,
        )
        try:
            recording._viewer = hub.add_viewer(notify=recording._wake, internal=True)
        except BaseException:
            recording._file.close()
            target.unlink(missing_ok=True)
            raise
        recording._thread.start()
        recording._wake()  # what the hub pushed while the viewer was being added (the codec line)
        return recording

    # ---- the viewer's side (the hub's reader thread) -----------------------------------------------------------

    def _wake(self) -> None:
        """The viewer has something: move it out of the viewer's queue (cheap, no I/O) and wake the writer."""
        with self._cv:
            self._drain_locked()
            self._cv.notify_all()

    def _drain_locked(self) -> None:
        viewer = self._viewer
        if viewer is None:
            return
        while (frame := viewer.pop()) is not None:
            if self._overflow:
                continue  # given up: keep the viewer's queue empty until it is detached
            self._pending.append(frame)
            self._pending_bytes += frame.size
            if self._pending_bytes > self._max_pending:
                self._overflow = True
                self._error = (
                    f"the disk could not keep up: more than {self._max_pending} bytes were waiting to be written"
                )
                self._pending.clear()
                self._pending_bytes = 0

    # ---- the writer thread ---------------------------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._loop()
        except Exception as e:  # whatever goes wrong ends this recording, not the exporter
            log.exception("recording %s failed", self._path.name)
            self._error = self._error or f"{type(e).__name__}: {e}"
            self._reason = self._reason or REASON_ERROR
        finally:
            self._finalize()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while True:
                    # read first: a closed viewer takes nothing more, so this drain is the last
                    closed = self._viewer.closed
                    self._drain_locked()
                    if self._pending or self._stop or self._overflow or closed or self._late():
                        break
                    self._cv.wait(self._wait_s())
                batch = list(self._pending)
                self._pending.clear()
                self._pending_bytes = 0
                ending = self._stop or closed
            if self._overflow:
                self._reason = self._reason or REASON_ERROR
                return
            for frame in batch:
                if self._consume(frame):
                    return
            if ending:
                self._reason = self._reason or (REASON_STOP if self._stop else REASON_STREAM_ENDED)
                return
            if self._late():
                self._reason = self._reason or REASON_MAX_SECONDS
                return

    def _late(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

    def _wait_s(self) -> float:
        if self._deadline is None:
            return 1.0
        return max(0.01, min(1.0, self._deadline - time.monotonic()))

    def _consume(self, frame: Any) -> bool:
        """Take one transport frame; returns whether the recording is complete."""
        for kind, body in self._parser.feed(frame.wire):
            if kind == KIND_CONFIG:
                try:
                    self._line = json.loads(body)
                except ValueError:
                    log.debug("a codec line that is not JSON in the stream")
                if self._muxer is not None and self._family() != self._muxer.family:
                    self._reason = REASON_CODEC_CHANGED  # another kind of picture: not for this file
                    return True
            elif kind == KIND_MEDIA:
                ts_us, access_unit = split_media(body)
                if self._add(ts_us, access_unit, bool(getattr(frame, "idr", False))):
                    return True
        return False

    def _family(self) -> str:
        """The codec family the last codec line named (H.264 until one has)."""
        try:
            return codec_family(self._line["codec"])
        except (KeyError, ValueError):
            return DEFAULT_CODEC

    def _add(self, ts_us: int, access_unit: bytes, idr: bool) -> bool:
        if self._muxer is None:
            family = self._family()
            if family == "mjpeg":  # the stream did not say its codec when this recording began
                self._error = _MJPEG_NOT_RECORDED
                return True
            self._muxer = Fmp4Muxer(
                width=self._line.get("width"),
                height=self._line.get("height"),
                fragment_ms=self._fragment_ms,
                family=family,
            )
        muxer = self._muxer
        chunk = muxer.add(ts_us, access_unit, keyframe=True if idr else None)
        if self._max_seconds is not None and self._deadline is None and muxer.frames:
            self._deadline = time.monotonic() + self._max_seconds + _WALL_GRACE_S
        self._write(chunk)
        if self._max_us is not None and muxer.duration_us >= self._max_us:
            self._reason = REASON_MAX_SECONDS
            return True
        if self._max_bytes is not None and self._written >= self._max_bytes:
            self._reason = REASON_MAX_BYTES
            return True
        return False

    def _write(self, chunk: bytes) -> None:
        if chunk:
            try:
                self._file.write(chunk)
                self._file.flush()  # a killed process leaves everything written so far in the file
            except OSError:
                self._io_failed = True
                raise
            self._written += len(chunk)

    def _finalize(self) -> None:
        """Detach, write the last picture and the length, close; whatever failed before, the file is closed."""
        try:
            self._hub.remove_viewer(self._viewer)
        except Exception:
            log.debug("could not detach the recording's viewer", exc_info=True)
        muxer = self._muxer
        try:
            if muxer is not None and muxer.started and not self._io_failed:
                self._write(muxer.finish())
                for offset, data in muxer.duration_patches():
                    self._file.seek(offset)
                    self._file.write(data)
                self._file.flush()
        except Exception as e:  # noqa: BLE001 (the file is closed and the error reported, whatever it was)
            self._error = self._error or f"{type(e).__name__}: {e}"
            self._reason = REASON_ERROR
        try:
            self._file.close()
        except OSError as e:
            self._error = self._error or f"{type(e).__name__}: {e}"
        if self._error:
            self._reason = REASON_ERROR
        stats = self._viewer.stats() if self._viewer is not None else {}
        resyncs = stats.get("resyncs", stats.get("skips", 0))
        try:
            size = self._path.stat().st_size
        except OSError:
            size = self._written
        self._result = {
            "name": self._path.name,
            "path": str(self._path),
            "size": size,
            "seconds": round(muxer.duration_us / 1_000_000, 3) if muxer else 0.0,
            "frames": muxer.frames if muxer else 0,
            "width": muxer.width if muxer else 0,
            "height": muxer.height if muxer else 0,
            "reason": self._reason or REASON_STOP,
            "error": self._error,
            "size_changes": muxer.size_changes if muxer else 0,
            "resyncs": resyncs,
        }
        self._done.set()

    # ---- the caller's side ---------------------------------------------------------------------------------------

    @property
    def path(self) -> str:
        """The file being written."""
        return str(self._path)

    @property
    def active(self) -> bool:
        """Whether it is still recording."""
        return not self._done.is_set()

    def status(self) -> dict[str, Any]:
        """``{"active", "seconds", "frames", "bytes", "path", "reason", "error"}``.

        ``reason`` is ``None`` while the recording is active.
        """
        if self._result is not None:
            r = self._result
            return {
                "active": False,
                "seconds": r["seconds"],
                "frames": r["frames"],
                "bytes": r["size"],
                "path": r["path"],
                "reason": r["reason"],
                "error": r["error"],
            }
        muxer = self._muxer
        return {
            "active": True,
            "seconds": round(muxer.duration_us / 1_000_000, 3) if muxer else 0.0,
            "frames": muxer.frames if muxer else 0,
            "bytes": self._written,
            "path": str(self._path),
            "reason": None,
            "error": self._error,
        }

    def stop(self) -> dict[str, Any]:
        """End the recording (if it still runs), finish the file and return what it holds; again, it returns the same.

        Returns:
            ``{"name", "path", "size", "seconds", "frames", "width", "height", "reason", "error", "size_changes",
            "resyncs"}``: ``name`` is the file's name and ``path`` where it is; ``width`` and ``height`` are the
            picture size the file's header names (the first picture's); ``frames`` is 0 and the file empty if no key
            picture came; ``reason`` is ``stop``, ``max_seconds``, ``max_bytes``, ``stream_ended``, ``codec_changed``
            (the stream went on in another codec family) or ``error``.

        Raises:
            TimeoutError: If the writer does not finish in 15 seconds (the disk is hung); the recording is detached
                from the hub.
        """
        with self._cv:
            if not self._stop:
                self._stop = True
            self._cv.notify_all()
        try:
            # nothing more is queued; what was queued is already in the recording's list
            self._hub.remove_viewer(self._viewer)
        except Exception:
            log.debug("could not detach the recording's viewer", exc_info=True)
        self._thread.join(_JOIN_TIMEOUT_S)
        if self._result is None:
            raise TimeoutError("the recording did not finish: the disk may be hung")
        return dict(self._result)

    def __enter__(self) -> Self:
        """Context manager: the recording is stopped when the block ends."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Stop the recording."""
        self.stop()


def _checked_path(path: str | os.PathLike[str], root: str | os.PathLike[str] | None) -> Path:
    """Where the file goes: a plain file name in an existing directory, inside ``root`` when one is given."""
    wanted = Path(path)
    if root is None:
        directory = wanted.parent.resolve(strict=True)
    else:
        base = Path(root).resolve(strict=True)
        if not wanted.is_absolute():
            wanted = base / wanted
        if ".." in wanted.parts:
            raise ValueError("the recording's path may not contain '..'")
        directory = wanted.parent.resolve(strict=True)
        if directory != base and base not in directory.parents:
            raise ValueError(f"the recording's path must be inside {base}")
    if wanted.name in ("", ".", ".."):
        raise ValueError("the recording's path must name a file")
    return directory / wanted.name


__all__ = [
    "REASON_CODEC_CHANGED",
    "REASON_ERROR",
    "REASON_MAX_BYTES",
    "REASON_MAX_SECONDS",
    "REASON_STOP",
    "REASON_STREAM_ENDED",
    "Recording",
]
