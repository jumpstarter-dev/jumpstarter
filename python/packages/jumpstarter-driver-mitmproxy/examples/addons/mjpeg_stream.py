"""
Custom addon: MJPEG video stream simulation.

Serves a multipart MJPEG stream that simulates an IP camera feed.
The client's video player receives a continuous stream of JPEG frames
over HTTP, just like a real IP camera endpoint.

Mock config entry::

    "GET /streaming/video/camera/*": {
        "addon": "mjpeg_stream",
        "addon_config": {
            "frames_dir": "video/frames",
            "fps": 15,
            "default_resolution": [640, 480],
            "cameras": {
                "rear": {"frames_dir": "video/rear"},
                "surround": {"frames_dir": "video/surround"}
            }
        }
    }

File layout::

    mock-files/
    └── video/
        ├── frames/            ← default fallback frames
        │   ├── frame_000.jpg
        │   ├── frame_001.jpg
        │   └── ...
        ├── rear/              ← camera-specific frames
        │   ├── frame_000.jpg
        │   └── ...
        └── test_pattern.jpg   ← single-image fallback

If no frame files exist, the addon generates a minimal JPEG test
pattern so the client's video pipeline still exercises its
full decode/render path.
"""

from __future__ import annotations

import io
import time
from pathlib import Path

from mitmproxy import ctx, http

# ── Minimal JPEG generator (no PIL needed) ──────────────────

# A tiny valid JPEG: 8x8 pixels, solid gray.
# This is the smallest valid JFIF file that any decoder will accept.
MINIMAL_JPEG = bytes([
    0xFF, 0xD8, 0xFF, 0xE0,  # SOI + APP0
    0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,  # JFIF header
    0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
    0xFF, 0xDB, 0x00, 0x43, 0x00,  # DQT
    *([0x08] * 64),               # Quantization table (uniform)
    0xFF, 0xC0, 0x00, 0x0B,  # SOF0
    0x08,                     # 8-bit precision
    0x00, 0x08, 0x00, 0x08,  # 8x8 pixels
    0x01,                     # 1 component (grayscale)
    0x01, 0x11, 0x00,        # Component: ID=1, sampling=1x1, quant=0
    0xFF, 0xC4, 0x00, 0x1F, 0x00,  # DHT (DC)
    0x00, 0x01, 0x05, 0x01, 0x01, 0x01, 0x01, 0x01,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B,
    0xFF, 0xC4, 0x00, 0xB5, 0x10,  # DHT (AC)
    0x00, 0x02, 0x01, 0x03, 0x03, 0x02, 0x04, 0x03,
    0x05, 0x05, 0x04, 0x04, 0x00, 0x00, 0x01, 0x7D,
    *([0x00] * 162),
    0xFF, 0xDA, 0x00, 0x08,  # SOS
    0x01, 0x01, 0x00, 0x00, 0x3F, 0x00,
    # Compressed data: single MCU, gray
    0x7B, 0x40,
    0xFF, 0xD9,  # EOI
])


def _generate_test_pattern_jpeg(
    width: int = 640, height: int = 480, frame_num: int = 0,
) -> bytes:
    """Generate a JPEG test pattern.

    If Pillow is available, generates a proper test pattern with
    frame number overlay. Otherwise, returns the minimal JPEG.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (width, height), color=(40, 40, 40))
        draw = ImageDraw.Draw(img)

        # Grid lines
        for x in range(0, width, 80):
            draw.line([(x, 0), (x, height)], fill=(80, 80, 80))
        for y in range(0, height, 80):
            draw.line([(0, y), (width, y)], fill=(80, 80, 80))

        # Color bars at top
        bar_w = width // 7
        colors = [
            (255, 255, 255), (255, 255, 0), (0, 255, 255),
            (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255),
        ]
        for i, color in enumerate(colors):
            draw.rectangle(
                [i * bar_w, 0, (i + 1) * bar_w, height // 4],
                fill=color,
            )

        # Frame counter and timestamp
        timestamp = time.strftime("%H:%M:%S")
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 24)
        except (OSError, IOError):
            font = ImageFont.load_default()

        text = f"MOCK CAMERA  Frame: {frame_num:06d}  {timestamp}"
        draw.text(
            (20, height // 2 - 12), text,
            fill=(0, 255, 0), font=font,
        )

        # Moving element (proves the stream is updating)
        x_pos = (frame_num * 5) % width
        draw.ellipse(
            [x_pos - 15, height - 60, x_pos + 15, height - 30],
            fill=(255, 0, 0),
        )

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75)
        return buf.getvalue()

    except ImportError:
        # Pillow not available, use minimal JPEG
        return MINIMAL_JPEG


# ── MJPEG streaming handler ─────────────────────────────────


class Handler:
    """MJPEG video stream mock handler.

    Serves:
    - Stream: /streaming/video/camera/{camera_id}/stream.mjpeg
    - Snapshot: /streaming/video/camera/{camera_id}/snapshot.jpg

    For the stream endpoint, mitmproxy's response streaming is used
    to deliver frames continuously without buffering the entire
    response. The stream uses multipart/x-mixed-replace, which is
    the standard MJPEG-over-HTTP format supported by most embedded
    video players.
    """

    def __init__(self):
        self._frame_counters: dict[str, int] = {}

    def handle(self, flow: http.HTTPFlow, config: dict) -> bool:
        """Route video requests to the appropriate handler."""
        path = flow.request.path
        parts = path.rstrip("/").split("/")

        # Expected: /streaming/video/camera/{camera_id}/{resource}
        if len(parts) < 6:
            return False

        camera_id = parts[4]
        resource = parts[5]

        cameras = config.get("cameras", {})
        camera_config = cameras.get(camera_id, {})
        fps = config.get("fps", 15)
        resolution = config.get("default_resolution", [640, 480])
        frames_dir = camera_config.get(
            "frames_dir", config.get("frames_dir", "video/frames"),
        )
        files_dir = Path(
            config.get("files_dir", "/opt/jumpstarter/mitmproxy/mock-files")
        )

        if resource == "snapshot.jpg":
            self._serve_snapshot(
                flow, camera_id, frames_dir, files_dir, resolution,
            )
            return True

        elif resource == "stream.mjpeg":
            self._serve_mjpeg_stream(
                flow, camera_id, frames_dir, files_dir, resolution, fps,
            )
            return True

        return False

    def _serve_snapshot(
        self,
        flow: http.HTTPFlow,
        camera_id: str,
        frames_dir: str,
        files_dir: Path,
        resolution: list[int],
    ):
        """Serve a single JPEG snapshot."""
        frame = self._get_frame(camera_id, frames_dir, files_dir, resolution)

        flow.response = http.Response.make(
            200,
            frame,
            {
                "Content-Type": "image/jpeg",
                "Cache-Control": "no-cache",
            },
        )
        ctx.log.info(f"Camera snapshot: {camera_id}")

    def _serve_mjpeg_stream(
        self,
        flow: http.HTTPFlow,
        camera_id: str,
        frames_dir: str,
        files_dir: Path,
        resolution: list[int],
        fps: int,
    ):
        """Serve a multipart MJPEG stream.

        This uses mitmproxy's chunked response to deliver a
        continuous stream of JPEG frames. The client's video
        player will receive:

            --frame
            Content-Type: image/jpeg
            Content-Length: {size}

            <jpeg bytes>

        ...repeating for each frame.

        NOTE: mitmproxy buffers the full response by default.
        For true streaming, this generates a limited burst of
        frames (e.g., 5 seconds worth). For continuous streaming
        in production, consider using the responseheaders hook
        with flow.response.stream = True, or run a dedicated
        MJPEG server alongside mitmproxy.
        """
        boundary = "frame"
        burst_duration_s = 10  # Generate 10 seconds of frames
        num_frames = int(burst_duration_s * fps)

        parts = []
        for _ in range(num_frames):
            frame = self._get_frame(
                camera_id, frames_dir, files_dir, resolution,
            )
            parts.append(
                f"--{boundary}\r\n"
                f"Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(frame)}\r\n"
                f"\r\n".encode()
                + frame
                + b"\r\n"
            )

        body = b"".join(parts)
        body += f"--{boundary}--\r\n".encode()

        flow.response = http.Response.make(
            200,
            body,
            {
                "Content-Type": f"multipart/x-mixed-replace; boundary={boundary}",
                "Cache-Control": "no-cache, no-store",
                "Connection": "keep-alive",
            },
        )
        ctx.log.info(
            f"MJPEG stream: {camera_id} "
            f"({num_frames} frames, {fps} fps)"
        )

    def _get_frame(
        self,
        camera_id: str,
        frames_dir: str,
        files_dir: Path,
        resolution: list[int],
    ) -> bytes:
        """Get the next frame for a camera.

        Tries to load from disk, cycling through available files.
        Falls back to generated test pattern.
        """
        counter = self._frame_counters.get(camera_id, 0)
        self._frame_counters[camera_id] = counter + 1

        # Try loading from files directory
        frame_dir = files_dir / frames_dir

        if frame_dir.exists():
            frames = sorted(frame_dir.glob("*.jpg"))
            if not frames:
                frames = sorted(frame_dir.glob("*.jpeg"))
            if frames:
                frame_path = frames[counter % len(frames)]
                return frame_path.read_bytes()

        # Generate test pattern
        return _generate_test_pattern_jpeg(
            resolution[0], resolution[1], counter,
        )
