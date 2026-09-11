"""MJPEG capture via v4l2-ctl (uses libv4l2, native UVC JPEG without re-encode)."""

from __future__ import annotations

import logging
import queue
import shutil
import subprocess
import threading
from typing import Any

logger = logging.getLogger(__name__)


def _device_path(device: int | str) -> str:
    if isinstance(device, str):
        return device
    return f"/dev/video{device}"


def _extract_jpegs(buffer: bytearray, chunk: bytes) -> list[bytes]:
    buffer.extend(chunk)
    frames: list[bytes] = []
    while True:
        start = buffer.find(b"\xff\xd8")
        if start == -1:
            buffer.clear()
            break
        if start > 0:
            del buffer[:start]
        end = buffer.find(b"\xff\xd9", 2)
        if end == -1:
            break
        frames.append(bytes(buffer[: end + 2]))
        del buffer[: end + 2]
    return frames


class V4L2CtlMjpegCapture:
    """Capture MJPEG frames using ``v4l2-ctl --stream-mmap``."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=32)
        self._buffer = bytearray()
        self._stop = threading.Event()
        self._device = ""
        self._width = 0
        self._height = 0

    @property
    def is_open(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def open(self, device: int | str, width: int, height: int, fps: int) -> None:
        if shutil.which("v4l2-ctl") is None:
            raise OSError("v4l2-ctl not found (install v4l-utils)")

        if self.is_open:
            self.close()

        self._device = _device_path(device)
        self._width = width
        self._height = height
        self._stop.clear()

        self._thread = threading.Thread(target=self._reader_loop, name="v4l2-ctl-mjpeg", daemon=True)
        self._thread.start()
        logger.info(
            "v4l2-ctl MJPEG passthrough on %s (%sx%s @ %sfps)",
            self._device,
            width,
            height,
            fps,
        )

    def _start_process(self) -> subprocess.Popen[bytes]:
        cmd = [
            "v4l2-ctl",
            "-d",
            self._device,
            f"--set-fmt-video=width={self._width},height={self._height},pixelformat=MJPG",
            "--stream-mmap",
            "--stream-count=120",
            "--stream-to=-",
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        if proc.stdout is None:
            raise ConnectionError("v4l2-ctl did not provide stdout")
        self._proc = proc
        return proc

    def _reader_loop(self) -> None:
        try:
            while not self._stop.is_set():
                proc = self._start_process()
                stdout = proc.stdout
                assert stdout is not None
                while not self._stop.is_set():
                    chunk = stdout.read(65536)
                    if not chunk:
                        break
                    for frame in _extract_jpegs(self._buffer, chunk):
                        while not self._stop.is_set():
                            try:
                                self._queue.put(frame, timeout=0.1)
                                break
                            except queue.Full:
                                try:
                                    self._queue.get_nowait()
                                except queue.Empty:
                                    pass
                proc.wait(timeout=1)
                if self._proc is proc:
                    self._proc = None
        finally:
            self._stop.set()

    def get_resolution(self) -> tuple[int, int]:
        return self._width, self._height

    def read_jpeg(self) -> bytes:
        if not self.is_open:
            raise ConnectionError("v4l2-ctl capture not open")
        try:
            return self._queue.get(timeout=2.0)
        except queue.Empty as exc:
            raise ConnectionError("Timed out waiting for MJPEG frame from v4l2-ctl") from exc

    def discard_frames(self, count: int = 1) -> None:
        for _ in range(count):
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def close(self) -> None:
        self._stop.set()
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)
            self._proc = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._buffer.clear()
