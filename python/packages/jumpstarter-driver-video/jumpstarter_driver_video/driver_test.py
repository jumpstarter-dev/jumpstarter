import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import pytest
from PIL import Image

from jumpstarter_driver_video.driver import HttpVideo

from jumpstarter.common.utils import serve


def _jpeg_bytes(color: str) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="JPEG")
    return buf.getvalue()


FRAME_A = _jpeg_bytes("red")
FRAME_B = _jpeg_bytes("blue")
BOUNDARY = "frameboundary"


class FakeCameraHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/snapshot.jpg":
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(FRAME_A)))
            self.end_headers()
            self.wfile.write(FRAME_A)
        elif self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.end_headers()
            for frame in (FRAME_A, FRAME_B):
                self.wfile.write(f"--{BOUNDARY}\r\n".encode())
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
            self.wfile.write(f"--{BOUNDARY}--\r\n".encode())
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def fake_camera():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCameraHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)


def test_snapshot_via_snapshot_url(fake_camera):
    instance = HttpVideo(url=f"{fake_camera}/stream", snapshot_url=f"{fake_camera}/snapshot.jpg")

    with serve(instance) as client:
        assert client.snapshot_bytes() == FRAME_A
        assert client.snapshot().size == (8, 8)


def test_snapshot_from_mjpeg_stream(fake_camera):
    instance = HttpVideo(url=f"{fake_camera}/stream")

    with serve(instance) as client:
        assert client.snapshot_bytes() == FRAME_A


def test_stream_path(fake_camera):
    instance = HttpVideo(url=f"{fake_camera}/stream")

    with serve(instance) as client:
        assert client.stream_path() == "/stream"


def test_stream_tunnel(fake_camera):
    instance = HttpVideo(url=f"{fake_camera}/stream")

    async def read_stream(client, path):
        async with client.stream_async("connect") as tunnel:
            await tunnel.send(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("ascii"))
            buf = b""
            while FRAME_B not in buf:
                buf += await tunnel.receive()
            return buf

    with serve(instance) as client:
        buf = client.portal.call(read_stream, client, client.stream_path())
        assert b"multipart/x-mixed-replace" in buf
        assert FRAME_A in buf


def test_state_reports_online_and_resolution(fake_camera):
    instance = HttpVideo(url=f"{fake_camera}/stream", snapshot_url=f"{fake_camera}/snapshot.jpg")

    with serve(instance) as client:
        state = client.state()
        assert state.online is True
        assert (state.width, state.height) == (8, 8)


def test_state_reports_offline_when_source_unreachable():
    # nothing is listening on this port
    instance = HttpVideo(url="http://127.0.0.1:1/stream")

    with serve(instance) as client:
        state = client.state()
        assert state.online is False
        assert state.width is None


class FakeChunkedCameraHandler(BaseHTTPRequestHandler):
    """Serves MJPEG with chunked transfer encoding, like ESP-IDF cameras."""

    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path != "/stream":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for frame in (FRAME_A, FRAME_B):
            part = (
                f"--{BOUNDARY}\r\n".encode()
                + b"Content-Type: image/jpeg\r\n"
                + f"Content-Length: {len(frame)}\r\n\r\n".encode()
                + frame
                + b"\r\n"
            )
            chunk = f"{len(part):x}\r\n".encode() + part + b"\r\n"
            self.wfile.write(chunk)
        self.wfile.write(b"0\r\n\r\n")

    def log_message(self, format, *args):
        pass


@pytest.fixture
def fake_chunked_camera():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeChunkedCameraHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)


def test_stream_tunnel_chunked(fake_chunked_camera):
    """End-to-end: tunnel through an HttpVideo to a chunked MJPEG server."""
    instance = HttpVideo(url=f"{fake_chunked_camera}/stream")

    async def read_stream(client, path):
        async with client.stream_async("connect") as tunnel:
            await tunnel.send(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("ascii"))
            buf = b""
            while FRAME_B not in buf:
                buf += await tunnel.receive()
            return buf

    with serve(instance) as client:
        buf = client.portal.call(read_stream, client, client.stream_path())
        assert b"multipart/x-mixed-replace" in buf
        assert FRAME_A in buf


@pytest.mark.parametrize("url", ["ftp://camera/stream", "not-a-url"])
def test_invalid_url_rejected(url):
    with pytest.raises(ValueError):
        HttpVideo(url=url)
