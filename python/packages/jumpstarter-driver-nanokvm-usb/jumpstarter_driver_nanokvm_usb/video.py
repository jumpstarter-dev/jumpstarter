"""UVC video capture from NanoKVM-USB using OpenCV and optional V4L2 MJPEG passthrough."""

from __future__ import annotations

import base64
import logging
import shutil
import sys
from typing import Any, Literal

import cv2
import numpy as np
from numpy.typing import NDArray

from .v4l2_ctl_mjpeg import V4L2CtlMjpegCapture
from .v4l2_mjpeg import V4L2MjpegCapture

logger = logging.getLogger(__name__)

VideoFormat = Literal["mjpeg_passthrough", "jpeg"]


class VideoCapture:
    def __init__(self) -> None:
        self._cap: cv2.VideoCapture | None = None
        self._mjpeg: V4L2MjpegCapture | None = None
        self._jpeg_quality = 95

    @property
    def is_open(self) -> bool:
        if self._mjpeg is not None and self._mjpeg.is_open:
            return True
        return self._cap is not None and self._cap.isOpened()

    def open(
        self,
        device: int | str = 0,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        *,
        video_format: VideoFormat = "mjpeg_passthrough",
        jpeg_quality: int = 95,
    ) -> None:
        if self.is_open:
            self.close()

        self._jpeg_quality = max(1, min(100, int(jpeg_quality)))

        if video_format == "mjpeg_passthrough":
            if shutil.which("v4l2-ctl"):
                try:
                    ctl = V4L2CtlMjpegCapture()
                    ctl.open(device, width, height, fps)
                    self._mjpeg = ctl
                    return
                except OSError as exc:
                    logger.warning("v4l2-ctl MJPEG passthrough unavailable (%s)", exc)

            if sys.platform == "linux":
                try:
                    mjpeg = V4L2MjpegCapture()
                    mjpeg.open(device, width, height, fps)
                    self._mjpeg = mjpeg
                    return
                except OSError as exc:
                    logger.warning("V4L2 MJPEG passthrough unavailable (%s); falling back to OpenCV", exc)

        self._open_opencv(device, width, height, fps)

    def _open_opencv(
        self,
        device: int | str,
        width: int,
        height: int,
        fps: int,
    ) -> None:
        api = cv2.CAP_V4L2
        cap = cv2.VideoCapture(device, api)
        if not cap.isOpened() and isinstance(device, int):
            cap = cv2.VideoCapture(f"/dev/video{device}", api)

        if not cap.isOpened():
            raise ConnectionError(f"Cannot open video device: {device}")

        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
        self._cap = cap

        actual_w, actual_h = self.get_resolution()
        if actual_w != width or actual_h != height:
            logger.warning(
                "OpenCV requested %sx%s, device reports %sx%s",
                width,
                height,
                actual_w,
                actual_h,
            )

    def close(self) -> None:
        if self._mjpeg is not None:
            self._mjpeg.close()
            self._mjpeg = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def discard_stale_frames(self, count: int = 1) -> None:
        if self._mjpeg is not None and self._mjpeg.is_open:
            self._mjpeg.discard_frames(count)
            return
        if self._cap is None or not self._cap.isOpened():
            return
        for _ in range(count):
            self._cap.grab()

    def read_frame(self) -> NDArray[np.uint8]:
        jpeg = self.read_frame_jpeg()
        buffer = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if frame is None:
            raise ConnectionError("Failed to decode JPEG frame")
        return np.asarray(frame, dtype=np.uint8)

    def read_frame_rgb(self) -> NDArray[np.uint8]:
        frame = self.read_frame()
        return np.asarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), dtype=np.uint8)

    def read_frame_jpeg(self, quality: int | None = None) -> bytes:
        if self._mjpeg is not None and self._mjpeg.is_open:
            return self._mjpeg.read_jpeg()

        if self._cap is None or not self._cap.isOpened():
            raise ConnectionError("Video device not open")

        ret, frame = self._cap.read()
        if not ret or frame is None:
            raise ConnectionError("Failed to read frame from video device")

        q = quality if quality is not None else self._jpeg_quality
        ok, buf = cv2.imencode(".jpg", frame, (cv2.IMWRITE_JPEG_QUALITY, q))
        if not ok:
            raise RuntimeError("JPEG encoding failed")
        return buf.tobytes()

    def read_frame_base64(self, quality: int | None = None) -> str:
        jpeg_bytes = self.read_frame_jpeg(quality)
        return base64.b64encode(jpeg_bytes).decode("ascii")

    def get_resolution(self) -> tuple[int, int]:
        if self._mjpeg is not None and self._mjpeg.is_open:
            return self._mjpeg.get_resolution()
        if self._cap is None:
            raise ConnectionError("Video device not open")
        width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return width, height

    @staticmethod
    def list_devices(max_index: int = 10) -> list[dict[str, Any]]:
        devices: list[dict[str, Any]] = []
        for index in range(max_index):
            cap = cv2.VideoCapture(index)
            if cap.isOpened():
                devices.append(
                    {
                        "index": index,
                        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                        "fps": cap.get(cv2.CAP_PROP_FPS),
                        "backend": cap.getBackendName(),
                    }
                )
                cap.release()
        return devices
