import io
import webbrowser
from base64 import b64decode

import click
from aiohttp import web
from anyio import EndOfStream, get_cancelled_exc_class, move_on_after, sleep_forever
from PIL import Image

from .common import VideoState
from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group

LANDING_PAGE = """\
<!DOCTYPE html>
<html>
<head>
  <title>Video</title>
  <style>
    body { background: #1a1a1a; color: #eee; font-family: system-ui; margin: 0;
           display: flex; flex-direction: column; align-items: center; padding: 20px; }
    img { max-width: 100%; border: 1px solid #333; }
    a { color: #6cf; }
    .info { margin: 10px 0; font-size: 14px; color: #aaa; }
  </style>
</head>
<body>
  <h2>Jumpstarter Video Stream</h2>
  <img src="/stream" alt="Live video stream" />
  <p class="info"><a href="/snapshot">Single snapshot (JPEG)</a></p>
</body>
</html>
"""


def _parse_content_type(header_bytes: bytes) -> str:
    """Extract Content-Type from raw HTTP response headers."""
    for line in header_bytes.decode("ascii", errors="replace").split("\r\n"):
        if line.lower().startswith("content-type:"):
            return line.split(":", 1)[1].strip()
    return "multipart/x-mixed-replace; boundary=--"


def _is_chunked(header_bytes: bytes) -> bool:
    """Whether the response body uses HTTP chunked transfer encoding."""
    for line in header_bytes.decode("ascii", errors="replace").split("\r\n"):
        name, _, value = line.partition(":")
        if name.strip().lower() == "transfer-encoding" and "chunked" in value.lower():
            return True
    return False


async def _iter_body(tunnel, buf: bytes, chunked: bool):
    """Yield response body bytes from the tunnel, undoing chunked framing.

    Sources that serve MJPEG from an HTTP server which chunks its output (the
    ESP-IDF http server does) would otherwise leak chunk-size lines into the
    multipart body, corrupting the stream for the client.
    """
    if not chunked:
        if buf:
            yield buf
        while True:
            yield await tunnel.receive()

    while True:
        while b"\r\n" not in buf:
            buf += await tunnel.receive()
        size_line, _, buf = buf.partition(b"\r\n")
        try:
            size = int(size_line.split(b";")[0].strip(), 16)
        except ValueError:
            raise web.HTTPBadGateway(reason="invalid chunk size from upstream") from None
        if size == 0:
            return
        if size > _MAX_CHUNK_SIZE:
            raise web.HTTPBadGateway(reason="upstream chunk too large")
        while len(buf) < size + 2:
            buf += await tunnel.receive()
        yield buf[:size]
        buf = buf[size + 2 :]  # drop the CRLF terminating the chunk


def run_video_server(client, app, port, open_browser):
    """Run an aiohttp app, opening the browser and blocking until Ctrl+C."""
    runner = web.AppRunner(app)

    async def serve():
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", port)
            await site.start()

            addresses = runner.addresses
            if not addresses:
                raise RuntimeError("Video server started without a bound address")
            actual_port = int(addresses[0][1])
            url = f"http://127.0.0.1:{actual_port}"
            click.echo(f"Video stream available at: {url}")
            click.echo(f"Snapshot endpoint: {url}/snapshot")
            click.echo("Press Ctrl+C to stop.")

            if open_browser:
                webbrowser.open(url)

            await sleep_forever()
        finally:
            with move_on_after(2, shield=True):
                await runner.cleanup()

    try:
        client.portal.call(serve)
    except KeyboardInterrupt:
        click.echo("\nStopping video server.")


def _parse_status_code(header_bytes: bytes) -> int:
    """Extract HTTP status code from the first line of raw response headers."""
    first_line = header_bytes.decode("ascii", errors="replace").split("\r\n", 1)[0]
    parts = first_line.split(None, 2)
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return 0


_MAX_HEADER_SIZE = 16 * 1024
_MAX_CHUNK_SIZE = 16 * 1024 * 1024  # 16 MB — generous for MJPEG frames


async def proxy_mjpeg_stream(client, request, path):
    """Proxy the source's native MJPEG stream through the jumpstarter tunnel."""
    async with client.stream_async("connect") as tunnel:
        await tunnel.send(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("ascii"))

        buf = b""
        try:
            while b"\r\n\r\n" not in buf:
                if len(buf) > _MAX_HEADER_SIZE:
                    raise web.HTTPBadGateway(reason="upstream response headers too large")
                buf += await tunnel.receive()
        except EndOfStream:
            raise web.HTTPBadGateway(reason="upstream closed before sending complete headers") from None

        header_part, _, body_start = buf.partition(b"\r\n\r\n")

        status = _parse_status_code(header_part)
        if status == 0:
            raise web.HTTPBadGateway(reason="invalid upstream status line")

        response = web.StreamResponse(status=status)
        response.content_type = _parse_content_type(header_part)
        await response.prepare(request)

        try:
            async for chunk in _iter_body(tunnel, body_start, _is_chunked(header_part)):
                await response.write(chunk)
        except (EndOfStream, ConnectionResetError, ConnectionAbortedError, get_cancelled_exc_class()):
            pass

    return response


class VideoClient(DriverClient):
    """Client for video source drivers implementing VideoInterface."""

    def snapshot(self):
        """Get a snapshot image from the video input

        :return: PIL Image object of the snapshot image
        :rtype: PIL.Image
        """
        return Image.open(io.BytesIO(self.snapshot_bytes()))

    def snapshot_bytes(self) -> bytes:
        """Get raw JPEG bytes from the video input"""
        return b64decode(self.call("snapshot"))

    def stream_path(self) -> str:
        """HTTP path serving the MJPEG stream on the ``connect`` tunnel"""
        return self.call("stream_path")

    def state(self) -> VideoState:
        """Get state of the video source

        :return: common video source state
        :rtype: VideoState
        """
        return VideoState.model_validate(self.call("state"))

    def cli(self):
        @driver_click_group(self)
        def video():
            """Video capture and streaming"""
            pass

        @video.command()
        def state():
            """Show video source state"""
            s = self.state()
            click.echo(f"Online:     {s.online}")
            if s.width is not None and s.height is not None:
                click.echo(f"Resolution: {s.width}x{s.height}")
            if s.fps is not None:
                click.echo(f"FPS:        {s.fps}")

        @video.command()
        @click.option("-o", "--output", default="snapshot.jpg", help="Output file path")
        def snapshot(output):
            """Save a single snapshot to file"""
            img = self.snapshot()
            img.save(output)
            click.echo(f"Saved snapshot to {output}")

        @video.command()
        @click.option("-p", "--port", default=0, type=int, help="Local server port (0 = auto)")
        @click.option("--browser/--no-browser", default=True, help="Open in web browser")
        def stream(port, browser):
            """Start local MJPEG streaming server

            Proxies the source's native MJPEG stream through the jumpstarter
            tunnel. Frame rate is controlled by the video source.
            """
            path = self.stream_path()

            async def handle_index(request):
                return web.Response(text=LANDING_PAGE, content_type="text/html")

            async def handle_snapshot(request):
                data = b64decode(await self.call_async("snapshot"))
                return web.Response(body=data, content_type="image/jpeg")

            async def handle_stream(request):
                return await proxy_mjpeg_stream(self, request, path)

            app = web.Application()
            app.router.add_get("/", handle_index)
            app.router.add_get("/snapshot", handle_snapshot)
            app.router.add_get("/stream", handle_stream)

            run_video_server(self, app, port, browser)

        return video
