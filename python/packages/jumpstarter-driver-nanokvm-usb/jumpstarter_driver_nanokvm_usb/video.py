"""UVC video capture from NanoKVM-USB using OpenCV."""

from __future__ import annotations

import base64
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray


class VideoCapture:
    def __init__(self) -> None:
        self._cap: cv2.VideoCapture | None = None

    @property
    def is_open(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def open(
        self,
        device: int | str = 0,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
    ) -> None:
        if self._cap is not None:
            self.close()

        cap = cv2.VideoCapture(device)
        if not cap.isOpened():
            raise ConnectionError(f"Cannot open video device: {device}")

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
        self._cap = cap

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def read_frame(self) -> NDArray[np.uint8]:
        if self._cap is None or not self._cap.isOpened():
            raise ConnectionError("Video device not open")

        ret, frame = self._cap.read()
        if not ret or frame is None:
            raise ConnectionError("Failed to read frame from video device")

        return np.asarray(frame, dtype=np.uint8)

    def read_frame_rgb(self) -> NDArray[np.uint8]:
        frame = self.read_frame()
        return np.asarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), dtype=np.uint8)

    def read_frame_jpeg(self, quality: int = 85) -> bytes:
        frame = self.read_frame()
        ok, buf = cv2.imencode(".jpg", frame, (cv2.IMWRITE_JPEG_QUALITY, quality))
        if not ok:
            raise RuntimeError("JPEG encoding failed")
        return buf.tobytes()

    def read_frame_base64(self, quality: int = 85) -> str:
        jpeg_bytes = self.read_frame_jpeg(quality)
        return base64.b64encode(jpeg_bytes).decode("ascii")

    def get_resolution(self) -> tuple[int, int]:
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
