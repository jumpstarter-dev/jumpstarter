"""Shared JPEG frame pump so video stream and VNC reuse one UVC capture."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)


class FramePump:
    """Capture JPEG frames on a background thread and fan them out to waiters."""

    def __init__(self, capture: Callable[[], bytes], fps: int = 30) -> None:
        self._capture = capture
        self._fps = max(0, int(fps))
        self._cond = threading.Condition()
        self._latest: bytes | None = None
        self._generation = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def generation(self) -> int:
        with self._cond:
            return self._generation

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nanokvm-usb-frame-pump", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def wait_jpeg(
        self,
        timeout: float = 2.0,
        after_generation: int | None = None,
    ) -> tuple[bytes, int] | None:
        """Block until a JPEG is available.

        If ``after_generation`` is set, wait for a strictly newer frame.
        """

        def _ready() -> bool:
            if self._latest is None:
                return False
            if after_generation is None:
                return True
            return self._generation > after_generation

        with self._cond:
            if not _ready():
                self._cond.wait_for(_ready, timeout=timeout)
            if not _ready() or self._latest is None:
                return None
            return self._latest, self._generation

    def wait_n_frames(self, count: int, timeout: float = 5.0) -> bytes:
        """Wait for ``count`` new frames and return the last JPEG."""
        if count < 1:
            count = 1
        deadline = time.monotonic() + timeout
        gen = -1
        jpeg: bytes | None = None
        for _ in range(count):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            got = self.wait_jpeg(timeout=remaining, after_generation=gen)
            if got is None:
                break
            jpeg, gen = got
        if jpeg is None:
            raise ConnectionError("Timed out waiting for video frame")
        return jpeg

    def _loop(self) -> None:
        interval = 1.0 / self._fps if self._fps > 0 else 0.0
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                jpeg = self._capture()
            except Exception:
                logger.debug("frame pump capture failed", exc_info=True)
                if self._stop.wait(0.2):
                    break
                continue
            with self._cond:
                self._latest = jpeg
                self._generation += 1
                self._cond.notify_all()
            if interval > 0:
                remaining = interval - (time.monotonic() - started)
                if remaining > 0 and self._stop.wait(remaining):
                    break
